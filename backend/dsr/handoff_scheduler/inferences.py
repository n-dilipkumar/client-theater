"""Every judgement call WF-055 makes, named and served so a reviewer can disagree.

The research for WF-055 states the flow, the two request shapes, the two researched
calls, the two role names, the Additional Invitees and the Required toggle, and the
four open questions this registry exists for. It is silent about which calendar
operation combines the people on one path, about what happens when no path matches,
about which CRM fields an integrator may pass, and about whether this plugin owns
reassignment. Those gaps are product behaviour rather than comments, so they are
collected here and served at ``GET /api/wf-055/inferences`` instead of being buried
in the module that implements them.

Each entry carries ``decision``, ``why`` and ``change_if``. A reviewer who disagrees
has a named thing to disagree with and a stated consequence, rather than having to
reverse-engineer a diff.
"""

from __future__ import annotations

from typing import Any

from dsr.handoff_scheduler.availability import DERIVATION_ID


def _entry(
    entry_id: str,
    topic: str,
    question: str,
    decision: str,
    why: str,
    change_if: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "topic": topic,
        "question": question,
        "decision": decision,
        "why": why,
        "change_if": change_if,
        **extra,
    }


INFERENCES: tuple[dict[str, Any], ...] = (
    _entry(
        DERIVATION_ID,
        "per-path calendar combination",
        "The research says a routing path returns its own startTimes and that a Required "
        "invitee's availability is considered. It never says which calendar operation combines "
        "the assignee and that invitee. Which does this build use?",
        "An intersection. A slot is on offer when the path's assignee and every invitee whose "
        "Required toggle is on are free for its whole duration. A not-required invitee is added to "
        "the meeting and their calendar is never read.",
        "A path names one AE: 'region -> AE pod, product line -> AE'. There is no choice to make "
        "inside a path, so there is nothing for a union to choose between, and a union would offer "
        "an instant when the path's own AE is busy. The neighbouring workflow derives the opposite "
        "operation for a good reason: WF-054's distribution names a team, so a union is right "
        "there and a booking re-checks which member it landed on. Two workflows choosing opposite "
        "operations in one product is only defensible if the difference is written down. The gate "
        "set is also two to four real calendars, which intersects to a non-empty window over a "
        "working week, where a whole team would not.",
        "Use a union in path_window and drop the booking-time gate check. A path would then offer "
        "an instant its own AE is busy, and the SDR would book a meeting the AE cannot attend.",
        jev_audit_id="jev-20261004T065905-27100-45006",
        jev_verdict="pass",
        jev_confidence=1.0,
        jev_selected="intersection_with_gate_recheck",
    ),
    _entry(
        "inference_no_matching_path_is_a_refusal",
        "unmatched router",
        "The init call is documented to return 'one or more routing paths'. What happens when a "
        "router matches none of them?",
        "A refusal, answered 400, naming every declared path and why each one did not match, and "
        "listing the fields the router's match blocks read against the request that was sent. The "
        "routing record is not written.",
        "An empty path list is indistinguishable from a router that matches nothing and from a "
        "router that was never configured, and an SDR cannot tell an SDR's typo from a real "
        "misconfiguration. The refusal is the only place the per-path match report can be read. A "
        "matched path with no free time is answered differently, so the two cases never share a "
        "message.",
        "Return an empty path list and outcome 'no_paths' in engine.init_simple.",
    ),
    _entry(
        "inference_matched_path_with_no_free_time_is_still_returned",
        "no availability",
        "A path matched, but its AE (or a Required invitee) has no free time in the interval. Is "
        "that path returned?",
        "Yes. It comes back with an empty startTimes and the ids of the people who gate it who are "
        "busy. When every matched path is empty the outcome is 'no_availability' and the routing "
        "record is still written, so the SDR can see which paths were matched and why each is "
        "empty.",
        "The SDR's next move is to pick a different path, and a routing that hides its empty paths "
        "cannot support that move. A busy week is also a legitimate state rather than an error, "
        "which is the same reasoning WF-054 uses for a fully booked team. Writing the record means "
        "the answer is reproducible later rather than being re-derived from a calendar that has "
        "since moved.",
        "Refuse with HandoffError, or drop empty paths from the response, in engine.init_simple.",
    ),
    _entry(
        "inference_required_invitee_narrows_the_path",
        "the Required toggle",
        "The evidence says availability is not considered 'unless you toggle the Required button'. "
        "Does a not-required invitee narrow the path at all?",
        "No. Their calendar is not read. They are still recorded on the meeting, and they are "
        "reported under the path's ignored_user_ids so an SDR can see that the reason a path was "
        "empty was never their calendar.",
        "The quoted sentence is about display: the calendar the SDR is shown. Narrowing the window "
        "by an optional person's diary would be a different rule from the one the research states, "
        "and it would empty most paths, because the researched use is 'always invite an SE or "
        "manager' and a manager is rarely the emptiest calendar in a pod.",
        "Include not-required invitees in gating_user_ids in paths.gating_user_ids.",
    ),
    _entry(
        "inference_absent_required_means_not_required",
        "absent flags",
        "An invitee declared with no 'required' key. Does their availability narrow the path?",
        "No. Absent means not required. This is the one place in the package where an absent flag "
        "means the restrictive answer rather than the permissive one.",
        "The evidence gives the default explicitly: availability is not considered unless the "
        "button is toggled, so the untoggled state is the researched one. Defaulting the other way "
        "would make a manager's diary mandatory on every path that invites them, which is the "
        "opposite of what the evidence describes.",
        "Default DEFAULT_REQUIRED in paths.py to True.",
    ),
    _entry(
        "inference_crm_explicits_cannot_shadow_the_researched_fields",
        "CRM context",
        "crmExplicits is 'additional CRM context to pass through to routing rules'. Which fields "
        "may an integrator pass, and what happens if one collides with the researched fields?",
        "Any key at all, with no allow-list. The three researched keys (request_type, "
        "guest_email, crm_record_id) are written after the explicits, so an explicit can never "
        "shadow them. A colliding key is dropped from the rule context and reported in the "
        "response as shadowed_explicit_keys. The explicits are kept verbatim on the routing record.",
        "The research explicitly leaves the permitted set open, and a fixed list is the one design "
        "that defeats the feature: an integrator who needs a field this build did not think of would "
        "have to come back for a release. Refusing the collision outright would be worse than "
        "dropping the key, because the integrator's lead would fail to route for a reason that "
        "names neither their field nor the field that won. Keeping the explicits verbatim means "
        "nothing is lost and the context is reproducible.",
        "Refuse a colliding key in rules.build_context, or let the explicit win.",
    ),
    _entry(
        "inference_path_match_is_case_insensitive",
        "rule comparison",
        "A rule compares a declared value against a context value. Is the comparison exact?",
        "It strips surrounding whitespace and ignores case, for every declared field.",
        "An SDR typing a guest email into a web form will not reproduce the capitalisation the CRM "
        "record holds, and a rule that misses on capitalisation sends the lead nowhere with no "
        "error to read. The same applies to a region code typed by hand into an integration.",
        "Compare the raw strings in rules.value_matches.",
    ),
    _entry(
        "inference_empty_match_is_a_catch_all_path",
        "catch-all path",
        "How does an admin write the path that takes any lead the specific paths did not claim?",
        "A path with no match block matches every request. Declared order is the order paths are "
        "returned in, and every matched path is returned, so a catch-all never suppresses a more "
        "specific path.",
        "The research promises 'one or more routing paths', so a router that matched exactly one "
        "would leave an SDR with no route when a rule needed widening later. A required key cannot "
        "express 'and nothing else', so an empty block is the only spelling of a catch-all that "
        "does not need a sentinel value.",
        "Refuse a path with no match block in paths.validate_path.",
    ),
    _entry(
        "inference_one_workspace_is_one_pod",
        "pod partition",
        "'workspaces partition SDR/AE pods so a third party can run one router per pod'. Is a pod "
        "one workspace or several?",
        "One workspace is one pod. A workspace holds that pod's users and the routers declared for "
        "it.",
        "The researched init call is scoped to workspace/{workspaceId}, so the workspace is the "
        "unit the vendor scopes a request to, and the note says workspaces partition the pods. "
        "Reading a pod as several workspaces would mean a router declared in one of them could not "
        "see a user in another, which would make 'one router per pod' impossible to express without "
        "copying the users into every workspace.",
        "Allow a router to name users from several workspaces, by resolving users against the "
        "org rather than against one workspace.",
    ),
    _entry(
        "inference_this_plugin_does_not_own_reassignment",
        "reassignment",
        "The automations note says reassignment 'later respects your Handoff/ChiliCal User controls "
        "and the Distribution settings of the meeting booked'. Does this plugin own reassignment?",
        "No. It owns the handoff up to the booked meeting. What it owes a later reassignment is "
        "that every meeting names the workspace, the router, the path, the booker and the assignee "
        "it came from, so a reassignment reopens that same routing context rather than choosing a "
        "new router. WF-063 owns reassignment.",
        "Two workflows owning the same transition would mean two conflicting writers on one "
        "meeting, and the research describes reassignment as governed by configuration this "
        "workflow does not read. Storing the references is the whole of the obligation, and "
        "copying the router into the meeting would freeze it and make a router edit invisible to a "
        "later reassignment.",
        "Add a reassign route here, which would then collide with WF-063's own.",
    ),
    _entry(
        "inference_users_hold_a_roles_list",
        "user roles",
        "The research publishes two role names, Booker and Assignee. Does a user hold one role or "
        "several?",
        "A roles list. A user may hold both. Absent means both, so a workspace row written by an "
        "older importer is not silently unable to book and unable to be booked.",
        "The research names the two roles on the meeting, not on the user, and it does not say a "
        "user has only one. Inventing a third enum value such as 'both' would be a term the "
        "research does not publish, and it would leave a two-role user inexpressible. Defaulting "
        "absent to both keeps an older row usable, and an explicit empty list is still respected for "
        "a user nobody may book or assign.",
        "Read a single 'role' key instead, in workspaces.roles_of.",
    ),
    _entry(
        "inference_booking_refuses_when_the_gate_took_the_slot",
        "booking re-check",
        "The routing offered a slot. By the time the SDR books it, the AE has taken another "
        "meeting. What then?",
        "A refusal, answered 409, naming who took the slot. The routing is not advanced to a "
        "different AE, because there is nobody else on the path to advance to.",
        "The alternative WF-054 uses, advancing to the next eligible member, exists because a round "
        "robin names a team. A handoff path names one person, so advancing would hand the lead to an "
        "AE the SDR did not pick, which is the opposite of a handoff. Refusing leaves the SDR with "
        "the other paths the init call returned, which is the move the flow actually offers.",
        "Advance to another matched path in engine.schedule_simple.",
    ),
    _entry(
        "inference_booker_cannot_change_between_init_and_schedule",
        "the booker",
        "The researched schedule call carries booker/{userId} in its path, and the init call carries "
        "it too. What if they differ?",
        "A refusal, answered 409. The booker on the schedule call must be the booker the routing was "
        "opened for.",
        "The research says the meeting is booked 'with SDR as the booker', which is a property of "
        "the booking the SDR made. Letting a second user book over the first would put one SDR's "
        "routing on another SDR's name, and the audit row would name a user who opened nothing.",
        "Accept any booker with the booker role in engine.schedule_simple.",
    ),
    _entry(
        "inference_workspace_edit_can_stale_a_router",
        "stale references",
        "A router names an assignee and invitees on a workspace. The workspace is edited "
        "afterwards. When is the reference re-checked?",
        "The gate set is re-resolved against the workspace at every evaluation, and a gate user the "
        "workspace no longer carries is reported under the path's unresolved_user_ids rather than "
        "refusing the whole routing. The assignee is re-checked harder, at booking time, and a "
        "missing role or a disconnected calendar refuses the booking.",
        "A router is a reusable asset and a workspace is edited independently, so validating only at "
        "save time would let a stale router sit there producing wrong answers. Refusing the whole "
        "evaluation over one removed SE would cost the SDR every other path as well, which is why "
        "the softening is limited to invitees and not to the assignee: an assignee is the point of "
        "the path.",
        "Validate the workspace references only when the router is saved, in paths.validate_paths.",
    ),
    _entry(
        "inference_naive_timestamps_are_utc",
        "timestamps",
        "A busy block carries a timestamp with no offset. Which zone is it in?",
        "UTC, the same as every other timestamp in this package.",
        "A workspace declared in one zone and read in another must produce the same instants. "
        "Guessing the host's local zone would make the same workspace offer different slots on two "
        "machines.",
        "Read naive timestamps as local time in timeutil.parse.",
    ),
    _entry(
        "inference_slot_grid_alignment",
        "slot grid",
        "Which instants can a handoff start at?",
        "Every duration_minutes step from the interval's start, for slots that fit entirely inside "
        "it. The grid is aligned to the interval's own start, not to midnight.",
        "Aligning to midnight would silently drop the first part of any interval that does not "
        "begin on the hour, and an SDR would be told the AE is unavailable at a time the AE is free.",
        "Floor each slot to the hour in timeutil.grid.",
    ),
)

INFERENCE_IDS: tuple[str, ...] = tuple(str(entry["id"]) for entry in INFERENCES)


def describe() -> dict[str, Any]:
    """The whole registry, as served."""
    return {
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "ids": list(INFERENCE_IDS),
        "note": (
            "Each entry names a judgement call this build made rather than a behaviour the research "
            "stated. A reviewer who disagrees with one has the change to make named."
        ),
    }


def by_id(entry_id: str) -> dict[str, Any] | None:
    """One entry, or ``None``."""
    for entry in INFERENCES:
        if entry["id"] == entry_id:
            return dict(entry)
    return None
