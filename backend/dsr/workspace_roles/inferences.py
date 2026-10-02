"""Every judgement call WF-077's research left open, named and served over HTTP.

The research for this workflow is unusually *dense* - it states the role
vocabulary, three guard rails, the no-hierarchy rule, the 403 wording, the
wildcard refusal, the coarse grants, the plan gate's asymmetry and the rate-limit
header - and unusually silent about the frame around them. It publishes no record
shapes, no error code names beyond the three it quotes, no plan names, no rate
limit number, no authorization-code lifetime, and no statement of what a workspace
*is*.

Each entry below is one of those silences: what this build chose, why, how to
change it, and what it would break. The point is that a judgement call left as a
comment in a function body is one nobody re-reads, and a wrong one becomes product
behaviour without anyone noticing. The sourced rules are **not** repeated here -
they live in :mod:`dsr.workspace_roles.vocabulary` as ``*_QUOTE`` constants, and an
entry that restates a sourced rule is a rule that can drift from its source.

The entries a reviewer should look at hardest are, in order:

* ``no-hierarchy-is-literal`` - the rule most likely to be "helpfully" implemented
  with an expansion, which is the exact opposite of what the research says.
* ``granularity-is-a-guess`` - what a "workspace" is, which every other decision
  inherits.
* ``coarse-grants-are-not-wildcards`` - the reading that reconciles two apparently
  contradictory sentences on one vendor page.
"""

from __future__ import annotations

from typing import Any

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "granularity-is-a-guess",
        "topic": "what a 'workspace' is, given the product's own unit is a room",
        "basis": "The workflow's user flow says 'the workspace's member list'; the rest of this "
        "product is built on rooms.",
        "value": {
            "entity": "workspace",
            "keyed_by": "workspace_id",
            "identity": "an arbitrary string, seeded from the demo dataset's room ids",
            "no_workspace_record": True,
            "members_are_not_room_access": True,
        },
        "why": (
            "The research consistently says *workspace* where this product says *room*, and never "
            "defines the relationship. Creating a first-class workspace record would mean inventing "
            "a parent entity for rooms that five dozen other features already own, which is exactly "
            "the shared-file edit the contract forbids and the kind of migration the "
            "schema-flexibility rule exists to prevent. So a workspace is a string, and a membership "
            "is a record tagged with it. Note this is a *different* vocabulary from WF-004's "
            "`room_access`: that is a buyer's grant to enter a room, this is a staff member's role "
            "inside a workspace, and a person can be both."
        ),
        "change_it": "WorkspaceIdentity in engine.py.",
        "blast_radius": "Every membership, token and custom role is keyed by it. Widening it to a "
        "real record is a migration, not a change.",
    },
    {
        "id": "no-hierarchy-is-literal",
        "topic": "whether `documents.write` grants `documents.read`",
        "basis": "`documents.write` does **not** imply `documents.read`. Each scope is independent.",
        "value": {
            "write_implies_read": False,
            "read_implies_write": False,
            "prefix_expansion": False,
            "wildcard_expansion": False,
            "coarse_implies_fine": False,
            "a_token_with_every_scope_except_read_cannot_read": True,
        },
        "why": (
            "The research does not merely say write does not imply read; it gives the reason - "
            "write-only tokens for ingestion workers that push data in and must not read it back "
            "out. Any expansion of a scope into its siblings destroys that use case, and it "
            "destroys it silently: the token looks least-privilege on the screen and is not. So "
            "`token_has_scope` is set membership and nothing else, and `coverage_for` exists only "
            "to describe a token in the UI and is never consulted on the request path."
        ),
        "change_it": "scopes.token_has_scope. Adding a hierarchy there is a one-line change that "
        "silently widens every token ever minted.",
        "blast_radius": "Every scope-gated route. There is no compensating control.",
    },
    {
        "id": "coarse-grants-are-not-wildcards",
        "topic": "what `apis.all` actually grants",
        "basis": "`apis.read` / `apis.all` - Forward-compatible coarse grants. / Don't request `*` "
        "or wildcards; they're not supported.",
        "value": {
            "apis_read_covers": ["tokens.read"],
            "apis_all_covers": ["tokens.read", "tokens.write"],
            "apis_all_is_a_wildcard": False,
            "apis_all_unlocks_documents_write": False,
            "apis_all_unlocks_members_read": False,
            "coverage_is_enforced_not_just_described": True,
            "coverage_is_a_fixed_table": True,
        },
        "why": (
            "Two sentences from the same vendor page read as contradictory if `apis.all` means "
            "'everything': the page both offers it and forbids wildcards. The reading that fits "
            "both is that it is a *named* grant over the API-management endpoints, which is what "
            "'forward-compatible coarse grant' describes - forward compatibility is about surviving "
            "new endpoints in its own family, not about the whole product. So it grants the "
            "token-management permissions and nothing else. The alternative reading - 'apis.all "
            "means every scope' - would make the no-wildcard sentence false and would make the "
            "token-creation surface lie about the token it just minted.\n\n"
            "The mapping is enforced, not merely described. An earlier build treated it as "
            "display-only, on the reasoning that letting a coarse grant widen a check is "
            "implicit hierarchy. That made `apis.all` mintable and inert - a grant that unlocks "
            "nothing is not least privilege, it is a no-op wearing a grant's name, and it is the "
            "more dangerous of the two because it reads as a real permission. The mapping is now "
            "applied on the request path, and it stays non-wildcard: a fixed published table, only "
            "scopes the catalogue already names, never a prefix and never a parent of anything."
        ),
        "change_it": "vocabulary.COARSE_COVERAGE.",
        "blast_radius": "Every `apis.*` token. Widening it to a true wildcard is a security "
        "change, not a configuration one.",
    },
    {
        "id": "scope-miss-and-team-mismatch-share-one-403",
        "topic": "whether to distinguish the two causes of Papermark's 403",
        "basis": "The token is valid, but doesn't have the scope the endpoint requires *or* isn't "
        "authorized to act on the team you're addressing.",
        "value": {
            "http_status": 403,
            "code": "forbidden",
            "detail_is_the_researched_sentence": True,
            "structured_cause_in_body": ["missing_scopes", "member_forbidden"],
        },
        "why": (
            "The vendor cannot tell the two apart from the response, and neither can a client that "
            "matches on the message. Splitting them into two codes would be more informative and "
            "would break every integration written against the documented response. So the human "
            "message and the status are exactly as researched, and the cause this build actually "
            "found is reported in a structured field alongside - additive, and safe to ignore."
        ),
        "change_it": "scopes.Forbidden.",
        "blast_radius": "Every client matching on the 403 body.",
    },
    {
        "id": "plan-names-are-invented",
        "topic": "the plan vocabulary and which features are gated",
        "basis": "enabling a gated feature returns `403 forbidden_plan_feature` on create/update, "
        "while existing links keep working after a downgrade.",
        "value": {
            "levels": ["starter", "business", "enterprise"],
            "gated": {"tokens": "business", "sso": "enterprise", "part11": "enterprise"},
            "enforced_on": ["create", "update"],
            "enforced_on_use": False,
            "existing_grants_survive_downgrade": True,
        },
        "why": (
            "The research names the error code and the asymmetry, and no plan names at all. "
            "Inventing a plan ladder is unavoidable if the gate is to exist at all; the "
            "interesting decision is the asymmetry, and that one *is* sourced: a downgrade must not "
            "break a token that was legitimately minted, or every renewal cycle becomes an outage. "
            "So `require_plan` is called from create and update paths only and never from a check "
            "that merely exercises an existing grant - and there is a test that asserts exactly "
            "that, because it is the easiest rule here to implement backwards."
        ),
        "change_it": "vocabulary.GATED_FEATURES and PLAN_RANK.",
        "blast_radius": "Which create/update paths refuse. Widening a gate retroactively does "
        "nothing to existing grants, by design.",
    },
    {
        "id": "rate-limit-is-in-memory",
        "topic": "where the per-minute budget is counted",
        "basis": "429 `rate_limit_exceeded` with `X-RateLimit-Reset` / Your token has exceeded its "
        "per-minute budget.",
        "value": {
            "storage": "process memory, keyed by token id",
            "limit_per_minute": 600,
            "not_written_to_the_store": True,
            "header_on_success_too": True,
        },
        "why": (
            "The research documents the response, not the counter. Writing it to the store would put "
            "an audit row on a request that was *refused*, and the audit log's granularity is "
            "supposed to describe changes to product data; a governance counter is not product "
            "data. The cost of the choice is that the budget is per process, so N workers allow N "
            "times the budget. That is recorded here rather than hidden, because it is the kind of "
            "thing a reviewer must be told rather than discover. The 600/minute number is arbitrary; "
            "the header is not."
        ),
        "change_it": "scopes.RateLimiter, and the limit passed to it from the feature module.",
        "blast_radius": "Throttling accuracy under multiple workers.",
    },
    {
        "id": "authorization-code-lifetime",
        "topic": "how long a consent code lives, and what stops it being reused",
        "basis": "The research names the authorize and token endpoints and the consent screen, and "
        "says nothing about lifetime or reuse.",
        "value": {
            "ttl_seconds": 600,
            "single_use": True,
            "bound_to": ["workspace_id", "client_id", "scopes"],
            "narrowing_allowed": True,
            "widening_allowed": False,
            "redirect_uri_recorded": True,
            "redirect_uri_enforced": False,
            "spend_written_in_the_token_transaction": True,
        },
        "why": (
            "Every one of these is a security property, and none of them is sourced, so they are "
            "listed rather than buried. The two that matter most: the code is spent in the *same* "
            "transaction that mints the token, so a crash between the two writes cannot leave a "
            "code that still works; and a client may ask for *less* than it was consented to but "
            "never for more, which is the only asymmetry that is safe. The ten minutes is the RFC "
            "6749 recommendation, which the research does not quote."
        ),
        "change_it": "oauth.CODE_TTL_SECONDS and oauth.check_code.",
        "blast_radius": "Every OAuth exchange.",
    },
    {
        "id": "unknown-fields-are-refused",
        "topic": "whether an unknown request field is an error",
        "basis": "machine-readable request schemas with `additionalProperties: false` on every "
        "`*Request` component, so generated SDKs should already reject unknown fields client-side.",
        "value": {
            "unknown_fields_refused": True,
            "status": 422,
            "code": "unknown_field",
        },
        "why": (
            "The research reports this as a property of the vendor's SDKs, not as a server rule - "
            "which is why 'already reject ... client-side' reads oddly for a server behaviour. The "
            "server half is what makes the guarantee real: a client that is not a generated SDK "
            "gets the same answer. Refusing is the right direction because this product's records "
            "are schema-flexible JSON, so a typo'd field name would otherwise be stored as data "
            "and read back as though it meant something."
        ),
        "change_it": "The request models in the feature module.",
        "blast_radius": "Every write route. Note this is the one place where schema flexibility "
        "stops at the request boundary; it stops there deliberately, and not inside `records.data`.",
    },
    {
        "id": "sso-forces-nothing",
        "topic": "what wiring SSO does to a member who has not been moved to it",
        "basis": "Admin wires directory-driven SSO so staff authenticate through the company IdP "
        "rather than local credentials.",
        "value": {
            "sso_only_member_enabled_connection": "local credentials refused",
            "sso_only_member_disabled_connection": "local credentials allowed",
            "sso_only_member_outside_domains": "local credentials allowed",
            "local_member_any_state": "local credentials allowed",
        },
        "why": (
            "The sentence says staff authenticate through the IdP, and the only member for whom "
            "that is a *rule* rather than a preference is one already marked as directory "
            "authenticated. Forcing every member would make a misconfigured IdP an outage for the "
            "whole workspace, which is the opposite of a governance control. So the rule is "
            "narrow, and the two escape hatches (connection disabled, member outside the "
            "connection's domains) exist so that a partial rollout does not lock anyone out."
        ),
        "change_it": "sso.decide_local_login.",
        "blast_radius": "Sign-in. Widening this to force every member is a one-line change with a "
        "blast radius of every user in the workspace.",
    },
    {
        "id": "admin-means-every-permission",
        "topic": "what 'admin privileges' means for the last-admin guard rail",
        "basis": "The role of the last member with admin privileges in the workspace cannot be "
        "changed.",
        "value": {
            "admin_is": "a role granting every permission",
            "custom_role_can_be_admin": True,
            "partial_permission_role_is_admin": False,
            "owner_excluded_from_the_count": True,
        },
        "why": (
            "The research says *admin privileges*, not 'the role called Admin'. Reading it as the "
            "name would let a workspace demote its only all-permissions member while a custom role "
            "with the same rights remained - and the guard rail's whole purpose is that the "
            "workspace cannot be left with nobody who can administer it. The literal reading is "
            "also the safe one: a role missing one permission is not an admin, so the guard rail "
            "holds in more cases than a name-based check would."
        ),
        "change_it": "rules.is_admin.",
        "blast_radius": "Which demotions are refused. Loosening it is the change that could strand "
        "a workspace.",
    },
    {
        "id": "role-spelling-is-preserved",
        "topic": "whether roles are stored normalised or in the vendor's spelling",
        "basis": "The `role` field accepts either a built-in role name (`Admin`, `Manager`, "
        "`Member`, `Collaborator`) or the name of a custom role.",
        "value": {
            "stored_as_sent": True,
            "compared_case_insensitively": True,
            "seismic_manage_canonicalised_to_write": True,
            "unknown_role_refused": True,
        },
        "why": (
            "Two spellings have to be reconciled: the role name, which is a human-facing string a "
            "workspace may define itself, and the scope permission level, where Seismic's `manage` "
            "and Papermark's `write` are the same level. Roles are stored as sent, because an audit "
            "log that reads `admin` when the request said `Admin` has rewritten history, while "
            "comparisons fold case so a client sending the documented name works. Scopes go the "
            "other way and *are* canonicalised, because the no-hierarchy test is a set membership "
            "and two spellings of one level would make that test ambiguous."
        ),
        "change_it": "rules.role_key for comparison, vocabulary.BUILT_IN_ROLES for storage.",
        "blast_radius": "Every role read back out of the audit log.",
    },
    {
        "id": "the-catalogue-is-this-products",
        "topic": "whether a Seismic scope name such as `seismic.library.view` can be minted here",
        "basis": "Seismic documents the format `seismic.object.permission`; the same research cites "
        "Papermark saying 'The token endpoint will reject unknown scopes.'",
        "value": {
            "seismic_names_accepted": False,
            "seismic_names_served_as_examples": True,
            "reason": "they are an example of the format, not a grant this product issues",
            "alias_verbs_still_canonicalised": ["view", "manage"],
        },
        "why": (
            "The research uses two vendors to establish a *format* and a set of *rules*, and this "
            "product issues its own grants. Accepting `seismic.library.view` as a scope here would "
            "mint a token holding a grant that unlocks nothing in this product - which is the "
            "worst outcome, because the token would look least-privilege and be useless. The same "
            "research page that gives the format is the one that says unknown scopes are rejected "
            "at the token endpoint, so the two sentences agree once the format is read as a format "
            "rather than as a vocabulary. The verb synonyms are still honoured (`view` is read, "
            "`manage` is write) so a caller who transliterates a Seismic scope onto this catalogue "
            "gets the right permission level rather than a confusing rejection."
        ),
        "change_it": "vocabulary.SCOPES - adding a scope is adding a grant, which is a security "
        "decision, not a configuration one.",
        "blast_radius": "Every integrator whose scope names came from a different vendor's docs.",
    },
    {
        "id": "error-codes-are-this-builds",
        "topic": "the error code names, given the research quotes only three",
        "basis": 'an error envelope whose `code` is byte-stable - "Safe to switch on."',
        "value": {
            "researched_codes": ["forbidden", "forbidden_plan_feature", "rate_limit_exceeded"],
            "this_builds_codes": [
                "role_transition_refused",
                "role_forbidden",
                "scope_invalid",
                "token_invalid",
                "membership_not_found",
                "authorization_code_invalid",
                "sso_configuration_invalid",
                "unknown_field",
                "not_found",
            ],
            "envelope": {"error": "same as code", "code": "same as error", "detail": "human text"},
        },
        "why": (
            "The stability property is the sourced part; the names are not. The three the research "
            "quotes are used verbatim so a client written against the vendor matches. The rest are "
            "named per-error-type because one code for all of them would be the thing the "
            "byte-stable promise exists to prevent. `error` and `code` carry the same value because "
            "the shared frontend client reads `body.error || body.detail` while the research names "
            "`code` - one value, two keys, so neither reader has to know about the other."
        ),
        "change_it": "errors.py. Renaming a code is a breaking change for any client that matches "
        "on it, which is the point of naming them.",
        "blast_radius": "Every client that switches on a code.",
    },
    {
        "id": "part11-is-a-flag",
        "topic": "how much of 21 CFR Part 11 to build",
        "basis": "PandaDoc's own docs recommend delegating to a 21 CFR Part 11 mode for regulated "
        "workspaces, and gate its audit endpoint by role.",
        "value": {
            "part11_enabled": "a workspace flag",
            "gated_feature": "part11",
            "requires_plan": "enterprise",
            "audit_read_role_gated": True,
            "delegated_to": "the vendor, per the research's recommendation",
        },
        "why": (
            "The research says to *delegate*, and says the vendor gates its audit endpoint by role. "
            "Building Part 11 validation here would be building the thing the research tells you "
            "not to build. What is implemented is the part that is actually this workflow's: the "
            "role gate on the audit read - 'This endpoint is accessible to authorized workspace "
            "administrators only' - which is a real permission check, and the flag that says which "
            "workspaces are in regulated mode."
        ),
        "change_it": "The `part11` feature flag in vocabulary.GATED_FEATURES and the role gate in "
        "the feature module's audit route.",
        "blast_radius": "Who can read the role-change audit trail.",
    },
)


def describe_inferences() -> dict[str, Any]:
    """The whole table, as the page and the API serve it."""
    return {
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "note": (
            "The sourced rules are not repeated here; they live in vocabulary.py as *_QUOTE "
            "constants and are served by /vocabulary. An entry here that restates a sourced rule "
            "is a rule that can drift from its source."
        ),
    }
