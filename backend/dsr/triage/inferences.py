"""Every judgement call in WF-022, in one inspectable place.

The research is explicit about its own limits, and the build brief asks for that
distinction to be kept rather than blurred:

* ``docs/research/digital-sales-room-workflows/wf/WF-022.md`` documents the view
  as a **UI capability** - "Documented as a UI capability" is its own phrase under
  ``apis_hit`` - and gives two of the five default views a definition while
  leaving the other three named only.
* It names the ``properties``, ``workspaceFilters`` and ``workspaceDomainFilters``
  parameters and the ``id``/``object``/``url`` fallback, and nothing about
  comparison semantics, operator names, or what happens to a filter nobody can
  read.
* It states that the workspace type is "inherited from the template", and quotes
  the vendor as promising that "any **future** workspaces created from that
  template will be automatically categorized" - which leaves open whether an
  already-created workspace follows a later change to its template.
* It names the dynamic-workspace pattern - "Show or hide specific workspace
  sections based on what a customer has done in your product" - and no section
  list, no condition vocabulary and no precedence rule.

So a good part of what this build does is inference. A judgement call left as a
comment in a function body is one nobody re-reads, and a wrong one becomes
product behaviour without anyone noticing. Collected here instead, each entry is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``/api/wf-022/inferences``, so a
  reviewer can read the whole list instead of reconstructing it from a diff.

Nothing here is a migration, a typed column, or a new required field. It is a
list of ordinary JSON, exactly like everything else this feature stores, and it
is a *record* of a judgement rather than a mechanism that enforces one.
"""

from __future__ import annotations

from typing import Any

from dsr.triage import vocabulary as vocab
from dsr.triage.fields import VIEW_ACTIONS

#: The one line of the research that governs most of this feature.
SOURCED_QUOTE = (
    "Documented as a UI capability. Row data maps to GET "
    "https://api.dock.us/v1/workspaces (with `properties` for selected fields and "
    "`workspaceFilters`/`workspaceDomainFilters` params) plus GET /v1/deals / "
    "GET /v1/accounts."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "active-pipeline-two-arms",
        "topic": "what puts a workspace in the Active Pipeline view",
        "topic_note": "the rule that has to fall through",
        "basis": (
            "Sourced verbatim: 'Any workspace that has a \\'Sales\\' workspace type, or an "
            "opportunity or deal connected from your CRM.' The wording is a disjunction and "
            "its second arm is itself a disjunction."
        ),
        "value": {
            "arm_one": "dock.type is 'Sales' (a workspace property)",
            "arm_two": "a joined Salesforce opportunity or a joined HubSpot deal (a domain value)",
            "combine": "workspace_filters OR workspace_domain_filters",
            "arm_two_join": "or",
            "excluded": "a workspace with neither",
        },
        "why": (
            "Both arms are independent and both are needed. Folding arm one into arm two "
            "loses every Sales workspace nobody has connected to a CRM, which is most of a "
            "pipeline's first week; folding arm two into arm one loses the workspace a rep "
            "connected by hand. A rule that does not fall through to either arm is a bug "
            "somebody hits in production, so both are implemented and both are tested on "
            "their own, not only through the combined view."
        ),
        "change_it": (
            "DEFAULT_VIEWS['active-pipeline'] in dsr/triage/vocabulary.py - the two filter "
            "groups and the `match` mode. Nothing in the engine changes with it."
        ),
        "blast_radius": "Which workspaces the Active Pipeline view lists.",
    },
    {
        "id": "workspace-vs-domain-filters",
        "topic": "what separates workspaceFilters from workspaceDomainFilters",
        "basis": (
            "The research names both parameters and never defines either. It does say the row "
            "is a join of 'Dock workspace metadata + engagement metrics + CRM-synced "
            "opportunity/deal fields'."
        ),
        "value": {
            "workspace_filters": "predicates over dock.* and engagement.* - the workspace's own values",
            "workspace_domain_filters": (
                "predicates over salesforce.*, hubspot.* and order_form.* - values that exist "
                "only because a CRM object or an order form was joined in"
            ),
            "within_a_group": "conditions join with the group's own and/or",
            "between_groups": "the view's `match`: all (default) or any",
        },
        "why": (
            "The join in the research's own data-flow sentence has exactly these two halves, "
            "so splitting the parameters along it gives each a meaning rather than leaving "
            "them as two spellings of one thing. It also makes Active Pipeline's structure "
            "expressible: arm one is a workspace filter, arm two a domain filter, and `match` "
            "ORs them."
        ),
        "change_it": "Group side labels in compile_group(), and the group keys in views.py.",
        "blast_radius": "Which parameter name a filter is written under, and nothing else.",
    },
    {
        "id": "unknown-filter-is-dropped-unknown-sort-is-refused",
        "topic": "what happens to a filter or a sort this build cannot read",
        "basis": (
            "The research documents that views can be filtered and sorted and says nothing "
            "about malformed ones. There is no sourced behaviour to fall back on."
        ),
        "value": {
            "unknown_operator": "condition dropped, reported in problems[]",
            "unknown_field": "still evaluated, without a declared type",
            "known_field_wrong_operator": "condition dropped, reported in problems[]",
            "unknown_sort_field": "refused with a 422 naming the published columns",
            "why_the_asymmetry": (
                "dropping a filter condition can only widen the result set, and a widened set "
                "is visible in the row count; a sort has no safe fallback, because answering "
                "an order request with a different order is a wrong answer rather than a "
                "partial one"
            ),
        },
        "why": (
            "A triage table that silently drops a filter is showing a pipeline the view does "
            "not describe, and a rep acting on it is acting on fiction. A view that refuses to "
            "render because of one typo is worse still. Reporting the problem and carrying on "
            "is the only answer that keeps both honest."
        ),
        "change_it": (
            "_compile_condition() and validate_sort() in dsr/triage/filters.py. The policy "
            "name is also published at /vocabulary as `unknown_operator`."
        ),
        "blast_radius": "Every view's row set and order.",
    },
    {
        "id": "unknown-columns-are-carried",
        "topic": "what a view may name as a column",
        "basis": (
            "Sourced as an extensibility claim: 'the column/filter set is user-defined so new "
            "CRM fields flow through automatically'. No enum of valid columns is published."
        ),
        "value": {
            "unknown_column_key": "accepted, ordered with the rest, value null, flagged known=false",
            "duplicates": "first position kept, the repeat dropped and reported",
            "empty_column_list": "refused with a 422",
        },
        "why": (
            "The alternative - refuse a column this build has not catalogued - would mean a "
            "team cannot prepare a view for a CRM field their integration is about to start "
            "syncing, and would have to time the view's creation to the field's arrival. The "
            "cell shows empty and the column_meta says why."
        ),
        "change_it": "COLUMNS in dsr/triage/vocabulary.py, and describe_column() in views.py.",
        "blast_radius": "Which column picker options a client offers.",
    },
    {
        "id": "crm-columns-are-provider-gated",
        "topic": "what a Salesforce column shows on a HubSpot-linked workspace",
        "basis": (
            "The research lists the fields per CRM - 'Salesforce data: Opportunity Stage, "
            "Opportunity Created Date, Opp Amount, Opportunity Type' and 'Hubspot data: Deal "
            "Stage, Deal Type, Deal Closed Date, Deal Amount' - and says the CRM is one of "
            "Salesforce / HubSpot per workspace. It does not say what a field of the other "
            "CRM shows."
        ),
        "value": {
            "mismatched_provider": "null",
            "no_crm_link": "null",
            "rationale": "a stage from a different system is a wrong number on a live pipeline",
        },
        "why": (
            "Both CRMs are optional and per workspace, and the researched integration is "
            "per-workspace ('an opportunity or deal connected from your CRM'). Reading "
            "`opportunity_stage` off a HubSpot link would be reading a field the integration "
            "never wrote, and a rep triaging a close date they do not have is worse off than "
            "one who sees a blank cell."
        ),
        "change_it": "_crm_values() in dsr/triage/rows.py.",
        "blast_radius": "Eight columns on every row that has a CRM link.",
    },
    {
        "id": "engagement-is-derived-not-stored",
        "topic": "where Views, Actions and Last Client View come from",
        "basis": (
            "The research lists the three fields as 'Engagement analytics' columns and says "
            "CRM sync 'keeps the joined columns current without user action'. It does not "
            "define a view, an action, or a Last Client View."
        ),
        "value": {
            "source": "activity records scoped to the workspace",
            "views": "records whose action is a view kind",
            "actions": "every activity record",
            "last_client_view": "when a buyer last performed a view-kind action; null if never",
            "view_actions": list(VIEW_ACTIONS),
            "stored_anywhere": False,
        },
        "why": (
            "Deriving on read is what makes the 'without user action' promise true: there is "
            "no cached column to fall out of date, and a team that starts logging a new action "
            "kind gets a number that includes it immediately. A stored metric would need an "
            "invalidation path and would be wrong for the first read after every change."
        ),
        "change_it": "aggregate_engagement() and VIEW_ACTIONS in dsr/triage/rows.py and fields.py.",
        "blast_radius": "The three engagement columns, and every view that sorts on one.",
    },
    {
        "id": "type-inheritance-is-live-not-copied",
        "topic": "whether a template type change re-categorises existing workspaces",
        "basis": (
            "Sourced: 'Workspace type is inherited from the template so \"Any future "
            "workspaces created from that template will be automatically categorized\".' The "
            "quote says future, which can be read as a backdated exemption or as the "
            "automation's user-facing promise."
        ),
        "value": {
            "resolution": "live, on read: the workspace's own type, else its template's, else none",
            "workspace_override": "always wins and is never overwritten by a template change",
            "clearing": '{"type": null} returns a workspace to inheriting',
        },
        "why": (
            "Resolution rather than a copy is the reading that makes both halves of the quote "
            "true at once: the vendor is telling a rep they will not have to categorise new "
            "rooms, which is exactly what inheritance delivers, and a workspace typed by hand "
            "keeps its own value. The copy reading would leave every workspace created before "
            "a template was typed permanently uncategorised with no way back, which the "
            "sentence does not say. The override-wins rule is the part that matters most: a "
            "template edit must never re-categorise a workspace somebody decided about."
        ),
        "change_it": "effective_type() in dsr/triage/rows.py, and row_type()'s own_type.",
        "blast_radius": "dock.type on every row, and therefore the Active Pipeline view.",
    },
    {
        "id": "my-workspaces-sentinel",
        "topic": "how the My Workspaces default says 'mine'",
        "basis": (
            "The research names the view and names 'owner' as a filter dimension, but does not "
            "define the view's rule."
        ),
        "value": {
            "published_default": "workspace_filters with dock.owner is $me",
            "resolved_at": "creation, against the new view's owner",
            "stored": "the resolved owner, never the sentinel",
            "unresolved": "matches nothing, and reports a problem",
        },
        "why": (
            "The default is a template, and a template cannot know who is creating from it, so "
            "it needs a way to say 'the current user'. Resolving once at creation means the "
            "stored filter is a plain equality - readable, and queryable through the dynamic "
            "index with ?where={\"workspace_filters.conditions.0.value\": \"dana\"}. A stored "
            "sentinel that resolved per request would be re-interpreted every time the view "
            "were read, and an unresolvable one would have to match everything to be useful, "
            "which is the one answer that cannot be defended."
        ),
        "change_it": "ME in dsr/triage/vocabulary.py, substitute_me_in_raw() in filters.py.",
        "blast_radius": "The My Workspaces default and anything cloned from it.",
    },
    {
        "id": "clone-is-private",
        "topic": "who owns a cloned view and who can see it",
        "basis": (
            "Sourced on both sides: 'You can also clone existing views to make your own "
            "customized copy' and 'You can create private views for yourself or public views "
            "for your entire team.'"
        ),
        "value": {
            "clone_of_public": "private, owned by the actor",
            "clone_of_private": "private, owned by the actor",
            "name_default": "<source name> (copy)",
            "caller_may_override": "name, columns, filters, sort - but not visibility",
        },
        "why": (
            "A customised copy is a personal variant, and a copy of a team view that was "
            "itself public would let one rep's rearrangement become the team's view without "
            "anybody deciding that. Overridable fields are the ones the flow describes "
            "changing after a clone; visibility is not among them."
        ),
        "change_it": "clone_view() in dsr/triage/views.py.",
        "blast_radius": "Every cloned view.",
    },
    {
        "id": "public-views-are-team-editable",
        "topic": "who may change a public view",
        "basis": (
            "Sourced only as visibility: 'public views for your entire team'. The research "
            "says who may read a public view and nothing about who may write it."
        ),
        "value": {
            "private": "owner only, for read, write and delete",
            "public": "any user, for read, write and delete",
            "not_readable": "404 rather than 403, for a private view of another user",
        },
        "why": (
            "This product has no role vocabulary - no manager, no admin, no team membership - "
            "so any narrower rule would be a role model invented here rather than researched, "
            "and inventing one is how a feature acquires permissions nobody specified. The "
            "same reading is what makes the private case meaningful: a private view really is "
            "yours alone, which is the only distinction the research draws. 404 rather than 403 "
            "so a private view's existence is not confirmable from the status code. Recorded "
            "here because it is the entry a reviewer is most likely to want to narrow, and "
            "narrowing it later is one predicate: TriageBoard.can_read."
        ),
        "change_it": "TriageBoard.can_read() in dsr/triage/views.py.",
        "blast_radius": "Every view read, write and delete.",
    },
    {
        "id": "open-set-replaces-and-is-validated",
        "topic": "what 'we'll remember which views you had open' means as stored state",
        "basis": (
            "Sourced: 'The views you had open are unique to your user account. We'll remember "
            "which views you had open the next time you open the Workspaces dashboard.' The "
            "research does not define the payload, the lifecycle, or what happens to a view "
            "deleted in between."
        ),
        "value": {
            "keyed_on": "the user account, one record per actor",
            "write": "replaces the whole set",
            "active_view_id": "must be one of view_ids, or absent",
            "unknown_or_forbidden_id": "refused with a 422",
            "deleted_since": "reported in missing[], never breaks the dashboard",
        },
        "why": (
            "Replace rather than merge, because the set is a snapshot of the dashboard as it "
            "is now and a merge would keep a closed view in the remembered list for ever. "
            "Validating each id against what the actor may read keeps the remembered set from "
            "becoming a way to confirm a private view's id. Skipping rather than failing on a "
            "deleted view is the whole point of the promise: the dashboard has to open."
        ),
        "change_it": "remember_open_views() and open_views() in dsr/triage/views.py.",
        "blast_radius": "The dashboard's restored state.",
    },
    {
        "id": "sort-nulls-last-in-both-directions",
        "topic": "where a workspace with no value for the sort field goes",
        "basis": (
            "The research says views can be sorted by owner, workspace creation date, recent "
            "client activity, CRM stage and workspace type. It says nothing about empty cells."
        ),
        "value": {
            "null": "always last, ascending and descending alike",
            "ties": "broken on row id, so the order is stable across requests",
            "mixed_types": "compared as instants, then numbers, then text",
        },
        "why": (
            "A rep sorting by Last Client View is looking for the next call to make. A "
            "workspace nobody has opened is a different kind of row, not a very old one, and "
            "putting twenty of them at the top of a descending sort hides exactly the engaged "
            "pipeline the view exists to show. Nulls last in both directions also means "
            "flipping the direction does not move them, which is the behaviour that makes a "
            "sort control feel trustworthy. The id tie-break is what stops equal rows "
            "reshuffling on every refresh."
        ),
        "change_it": "sort_rows() in dsr/triage/filters.py.",
        "blast_radius": "Every view's row order.",
    },
    {
        "id": "is-not-requires-a-value",
        "topic": "what a negated filter matches",
        "basis": "No sourced behaviour. The operator set is this build's.",
        "value": {
            "is_not": "true only when the row has a value and it differs",
            "not_in": "true only when the row has a value and it is not in the list",
        },
        "why": (
            "Plain negation would make 'Opportunity Stage is not Closed Won' match every "
            "workspace with no Salesforce link at all - 40 unsynced rows quietly presented as "
            "pipeline. Requiring a value on both sides makes the negation mean what a rep "
            "means by it, at the cost of not being able to ask for 'has no stage' with this "
            "operator; is_empty and is_not_empty are there for that."
        ),
        "change_it": "evaluate() in dsr/triage/filters.py.",
        "blast_radius": "Every negated filter in every view.",
    },
    {
        "id": "dynamic-sections-reuse-the-filter-engine",
        "topic": "what a section rule is written in",
        "basis": (
            "Sourced as a pattern only: 'Dynamic workspaces: Show or hide specific workspace "
            "sections based on what a customer has done in your product', with "
            "PATCH /v1/workspace-pages/{id} and PATCH /v1/workspace-sections/{id}."
        ),
        "value": {
            "sections": "whatever the workspace declares in its own data.sections",
            "rule": "{section, visible_when: <filter group>}; the section is visible when it matches",
            "no_rule": "visible, reason 'no_rule'",
            "unusable_rule": "visible, and the problem is reported",
            "rule_for_an_undeclared_section": "reported, does nothing",
        },
        "why": (
            "The research gives a condition vocabulary nowhere, so writing a second one for "
            "sections would have doubled the ways to be wrong. Reusing the filter engine means "
            "one operator set, one failure policy, and the same 'the customer has done X' "
            "question a CRM column answers elsewhere in the feature. Declaring sections rather "
            "than publishing a fixed list invents no section names - the research names the "
            "pattern, not a taxonomy of sections."
        ),
        "change_it": "sections() and set_section_rules() in dsr/triage/views.py.",
        "blast_radius": "The section visibility endpoint and its rules.",
    },
    {
        "id": "order-form-presence-not-status",
        "topic": "what puts a workspace on the Deal Desk",
        "basis": "Sourced: 'Any workspaces that uses Dock's order forms'.",
        "value": {
            "predicate": "an order form is attached; its status is not part of the rule",
            "status_column": "shown, so a rep can see which need chasing",
        },
        "why": (
            "The sentence is about having used an order form, not about the form's current "
            "state. A voided order form still put the workspace on the deal desk - it is the "
            "row that most needs a rep to look at it. Folding status into the rule would hide "
            "precisely the rows the view exists to surface."
        ),
        "change_it": "DEFAULT_VIEWS['deal-desk'] in dsr/triage/vocabulary.py.",
        "blast_radius": "The Deal Desk view.",
    },
    {
        "id": "implementations-type-string",
        "topic": "which workspace type the Implementations view selects",
        "basis": (
            "The view is named in the flow and the dashboard is documented as combining 'all "
            "the analytics from your Dock sales deal rooms and customer onboarding plans'. No "
            "onboarding type string is published."
        ),
        "value": {
            "type": vocab.INFERRED_IMPLEMENTATION_TYPE,
            "flagged": "the default view is marked inferred: true",
        },
        "why": (
            "'Sales' is the only workspace-type string the research quotes, and it is used "
            "verbatim. The onboarding side has a view but no value, so one is chosen and "
            "flagged rather than presented as sourced - a team whose template types "
            "onboarding workspaces differently edits one constant."
        ),
        "change_it": "INFERRED_IMPLEMENTATION_TYPE in dsr/triage/vocabulary.py.",
        "blast_radius": "The Implementations view only.",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return entry
    return None


def describe() -> dict[str, Any]:
    """The whole registry, alongside the half of the workflow that is sourced.

    Both halves in one payload on purpose. The point of this endpoint is that a
    reader can see where the line falls, and that means showing the sourced
    vocabulary next to the inferred behaviour rather than only the latter.
    """
    return {
        "count": len(INFERENCES),
        "sourced_quote": SOURCED_QUOTE,
        "sourced": {
            "default_views": [view["label"] for view in vocab.DEFAULT_VIEWS],
            "default_views_with_a_researched_definition": [
                view["label"] for view in vocab.DEFAULT_VIEWS if not view["inferred"]
            ],
            "columns": [column["key"] for column in vocab.COLUMNS if column["sourced"]],
            "properties_fallback": ["id", "object", "url"],
            "api_params": ["properties", "workspaceFilters", "workspaceDomainFilters"],
            "view_actions": list(VIEW_ACTIONS),
        },
        "inferences": [dict(entry) for entry in INFERENCES],
    }
