"""Every inference this package makes, in one inspectable place.

The research for WF-039 is unusually specific about the wire and silent about
almost everything around it. It gives four endpoints, two reference syntaxes, one
rollback flag, one ordering flag and six sets of documented limits. It does not
say what a bundle record is called here, which of two rollbacks a room should
default to, what "a single actionable error" means when two unrelated
subrequests fail, or how a room can show a rollback at all with no CRM attached.

A judgement call left as a comment in a function body is one nobody re-reads, and
a wrong one becomes product behaviour without anyone noticing. Collected here
instead, each inference is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``/api/wf-039/inferences``, so a
  reviewer or a client reads the whole list instead of inferring it from a diff.

Nothing here is a migration, a typed column, or a new required field. It is a list
of ordinary JSON, exactly like everything else this package stores, and it is a
*record* of a judgement rather than a mechanism that enforces one.
"""

from __future__ import annotations

from typing import Any

from dsr.atomic_bundle.dialects import (
    DEFAULT_API_VERSION as DEFAULT_SALESFORCE_VERSION,
    DEFAULT_DATAVERSE_VERSION,
)
from dsr.atomic_bundle.planner import (
    WARN_IMPLICIT_DEPENDENCY,
)
from dsr.atomic_bundle.vocabulary import (
    POLICY_MEANING,
    POLICY_PARTIAL,
    POLICY_STRICT,
    SOURCED_GAPS,
    SOURCED_QUOTES,
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "bundle-record-shape",
        "topic": "what one record in a declared bundle looks like",
        "basis": (
            "[sourced] The flow is 'a single request containing Account → Contact → "
            "Opportunity subrequests in dependency order' and the data flow is 'a room "
            "bundle (account, contact, opportunity + field map)'. The research never "
            "states the field names of that structure."
        ),
        "value": {
            "records": [
                {
                    "reference_id": "refAccount",
                    "type": "Account",
                    "fields": {"Name": "Northwind Traders"},
                },
                {
                    "reference_id": "refContact",
                    "type": "Contact",
                    "fields": {"LastName": "Okonkwo", "AccountId": "@{refAccount.id}"},
                    "parent": {"reference": "refAccount", "field": "AccountId"},
                },
            ],
            "required": ["records[].reference_id", "records[].type"],
            "optional": [
                "records[].fields",
                "records[].parent.reference",
                "records[].parent.field",
                "records[].implicit_depends_on",
                "records[].collection.field",
                "records[].collection.records",
                "field_map",
            ],
        },
        "why": (
            "The research's own extensibility claim is the test: 'a deployment can add a "
            "4th record type without touching the transport'. A record that named a type "
            "and its fields, and hung off another record by reference id, is the smallest "
            "shape that makes that true - a 4th type is one more entry in a list, and the "
            "transport never learns the name."
        ),
        "change_it": (
            "plan_bundle / _steps in dsr/atomic_bundle/planner.py. The renderer reads only "
            "what the plan exposes, so a renamed key needs the planner and the preview."
        ),
        "blast_radius": "Every bundle, every plan, every rendering.",
    },
    {
        "id": "declared-order-is-dependency-order",
        "topic": "whether a dependency may point forwards in the declared list",
        "basis": (
            "[sourced] 'builds a single request containing Account → Contact → Opportunity "
            "subrequests in dependency order' and 'later subrequests reference earlier ones "
            "by id'. Both sentences put the reference after the thing it references."
        ),
        "value": {
            "forward_reference": "refused with a message naming both records",
            "self_reference": "refused",
            "unknown_reference": "refused",
            "reordering": "never - a plan is not topologically sorted at run time",
        },
        "why": (
            "The alternative is to accept any order and sort at plan time, which is a "
            "plausible connector. It is rejected because the preview shows the declared "
            "order as the order that will run: a bundle that has to be reordered to be "
            "valid is a bundle whose preview lied to the person who read it."
        ),
        "change_it": "The ordering check at the end of _steps in planner.py.",
        "blast_radius": "Bundle validation only. Nothing that already plans changes.",
    },
    {
        "id": "default-policy",
        "topic": "which rollback policy a bundle gets when nobody says",
        "basis": (
            "[sourced] both behaviours are documented and the research does not choose "
            "between them. The flow's closing sentence is the tie-breaker: 'On any failure "
            "the whole bundle rolls back (strict mode) and the room shows a single "
            "actionable error.'"
        ),
        "value": {
            "default": POLICY_STRICT,
            "meaning": POLICY_MEANING[POLICY_STRICT],
            "overridable_per_bundle": True,
            "overridable_per_commit": True,
        },
        "why": (
            "The workflow's stated purpose is atomicity - the ticket is 'write account + "
            "contact + opportunity as one atomic transaction'. A default that quietly "
            "left half a record set behind would make the feature name false in the common "
            "case. Partial stays one toggle away, because a tenant that wants throughput "
            "over consistency should be able to say so."
        ),
        "change_it": "plan_bundle's default in planner.py, or POLICY_STRICT in vocabulary.py.",
        "blast_radius": "Any commit that does not name a policy. Every preview and run.",
    },
    {
        "id": "partial-on-a-changeset",
        "topic": "what a partial policy means on a dialect with no partial mode",
        "basis": (
            "[sourced] the sObject Tree endpoint documents one behaviour: 'If an error "
            "occurs while creating a record, the entire request fails.' Dataverse: 'When "
            "multiple operations are contained in a change set, all the operations are "
            "considered atomic.'"
        ),
        "value": {
            "salesforce_sobject_tree": "partial refused with a message quoting the sentence",
            "dataverse_batch": "partial refused with a message quoting the sentence",
            "dataverse_continue_on_error": (
                "not built - it is section 7 of the research, a different workflow"
            ),
        },
        "why": (
            "Silently downgrading to 'whatever the endpoint does' would mean the run record "
            "said partial and the CRM did something else. Refusing makes the mismatch "
            "impossible to miss, and names the workflow that does have the feature."
        ),
        "change_it": "ATOMICITY in vocabulary.py, which is what the refusal reads.",
        "blast_radius": "Plan validation for two of the four dialects.",
    },
    {
        "id": "implicit-dependency-is-declared-not-inferred",
        "topic": "how a bundle says 'this subrequest has to run after that one for a reason no reference captures'",
        "basis": (
            "[sourced] 'Collation can cause issues if there are implicit but not explicit "
            "dependencies between items. For example, consider a request that creates an "
            "Account, a Contact related to the Account, and a custom object that has a "
            "trigger dependent on the account name. … set collateSubrequests to false.' The "
            "caveat is documented; nothing can observe a trigger from the outside, so the "
            "dependency has to be declared."
        ),
        "value": {
            "declaration": "records[].implicit_depends_on",
            "warning_when_collate_is_on": WARN_IMPLICIT_DEPENDENCY,
            "blocks_on_failure": False,
            "fix": "set collate_subrequests to false",
        },
        "why": (
            "The research calls this 'exactly the knob a connector exposes to let a tenant "
            "trade speed for ordering guarantees'. A knob is only honest if the connector "
            "can tell when it is needed, and a trigger inside the CRM is invisible from "
            "here. Declaring it is the only way the warning can reach the person who has to "
            "flip the setting. It deliberately does *not* block execution: making an "
            "implicit edge into a hard dependency would promise an ordering guarantee the "
            "platform has not given."
        ),
        "change_it": "WARNINGS and _warnings in planner.py.",
        "blast_radius": "The preview's warnings, and nothing else.",
    },
    {
        "id": "collation-is-modelled-not-simulated",
        "topic": "what a transport is expected to do about collateSubrequests",
        "basis": (
            "[sourced] 'set collateSubrequests to false' is only meaningful if true has the "
            "documented effect. The research says what that effect is - grouping subrequests "
            "of the same type."
        ),
        "value": {
            "local_crm": (
                "groups by record type, in first-appearance order, and fails a step that "
                "would run before its declared parent"
            ),
            "failure_code": "collation_violation",
            "urllib_transport": "passes the flag through untouched",
        },
        "why": (
            "Without a CRM behind it, the flag is a string in a JSON body and the researched "
            "caveat is a paragraph in a doc. Grouping by type is the simplest faithful model "
            "of the documented behaviour, and it makes the failure reachable: a bundle with a "
            "declared implicit dependency fails with a collation violation while collation is "
            "on, and succeeds with it off."
        ),
        "change_it": "LocalCrm._execution_order in dsr/atomic_bundle/transport.py.",
        "blast_radius": "The local CRM only. A real transport is unaffected.",
    },
    {
        "id": "actionable-error-is-one-entry",
        "topic": "which error the room shows when more than one subrequest failed",
        "basis": (
            "[sourced] 'the room shows a single actionable error'. The research says single "
            "and does not say which."
        ),
        "value": {
            "shown": "one entry",
            "chosen": (
                "the first failure in declared order, except that a collation violation is "
                "chosen ahead of a plain refusal"
            ),
            "kept": "every per-subrequest outcome, on the run record",
        },
        "why": (
            "The room is showing a rep one thing to fix; three error cards is the thing this "
            "workflow exists to avoid, since the whole point is that the bundle is one unit. "
            "A collation violation outranks a plain refusal because its fix is a setting "
            "rather than a mapping, and getting that wrong makes the next attempt fail the "
            "same way. Every outcome is still stored, so nothing is lost."
        ),
        "change_it": "_actionable in dsr/atomic_bundle/transport.py.",
        "blast_radius": "The run record's actionable_error, and the page's error panel.",
    },
    {
        "id": "hubspot-has-no-atomic-batch",
        "topic": "what a HubSpot bundle actually is, given the research cites no transaction",
        "basis": (
            "[sourced] the research cites 'POST /crm/v3/objects/contacts/batch/create with an "
            "associations array, or PUT /crm/v3/objects/{objectTypeId}/{recordId}/"
            "associations/{toObjectType}/{toObjectId}/{associationTypeId}' and documents no "
            "cross-object transaction."
        ),
        "value": {
            "rendered_as": "a sequence of requests, in order",
            "is_sequence": True,
            "atomic": False,
            "strict_policy": "compensates by deleting what it created, in reverse",
            "compensation_refusal": "reported on the run as compensation_refused",
        },
        "why": (
            "The other three dialects are one atomic request; pretending this one is too "
            "would be a claim the research does not support, and a tenant that trusted it "
            "would find orphaned deals after a partial failure. Saying `atomic: false` and "
            "then honouring strict as an undo is the honest version - and a refused delete "
            "is surfaced rather than swallowed, because that is the case where the rows "
            "really are still there."
        ),
        "change_it": "ATOMICITY['hubspot_associations'] in vocabulary.py, and _compensate in transport.py.",
        "blast_radius": "The HubSpot dialect's rendering, its preview, and its runs.",
    },
    {
        "id": "sobject-tree-body",
        "topic": "the sObject Tree request body",
        "basis": (
            "[not sourced] The research quotes the endpoint, four limits and the atomicity "
            "sentence, and no body. This is stated in the research's own gaps list."
        ),
        "value": {
            "shape": (
                '{"records": [{"attributes": {"type", "referenceId"}, …fields…, '
                '"<ChildType>": {"records": [ … ]}}]}'
            ),
            "link_by": "nesting, not a parent id written into the child",
            "applied_to": ["200 records across all trees", "five types", "five levels deep"],
        },
        "why": (
            "A tree endpoint whose body were left undefined would make the dialect a "
            "placeholder. Nesting is the shape the endpoint's name and its 'five levels "
            "deep' limit both describe, and it is enforced by the planner rather than "
            "trusted to the renderer. It is still this build's shape, so it is named here "
            "instead of presented as a reproduction."
        ),
        "change_it": "_render_tree / _tree_record in dsr/atomic_bundle/dialects.py.",
        "blast_radius": "The tree dialect's request body only. Limits and policy are sourced and independent.",
    },
    {
        "id": "dataverse-mime-framing",
        "topic": "the exact multipart framing of a $batch",
        "basis": (
            "[partly sourced] The research gives 'Content-Type: multipart/mixed', 'a "
            "changeset_* boundary making the operations atomic', and 'Content-ID: 1/2/3'. It "
            "gives no literal body, so the framing below is this build's reading of those "
            "four facts."
        ),
        "value": {
            "content_type": "multipart/mixed",
            "boundary": "changeset_ + a token derived from the plan, so renders are reproducible",
            "part_content_type": "application/http",
            "part_headers": ["Content-Transfer-Encoding: binary", "Content-ID: <n>"],
            "bind_property": "<parent field>@odata.bind = $n",
        },
        "why": (
            "The four sourced facts pin the shape: a mixed-type envelope, a boundary that "
            "starts with changeset_, a numeric Content-ID per part, and a $n in the body. "
            "The boundary token is derived rather than random so a preview is byte-identical "
            "between renders - a UUID would make every preview a diff."
        ),
        "change_it": "_render_dataverse in dialects.py, and CHANGESET_PREFIX.",
        "blast_radius": "The Dataverse request body. The atomicity it carries is sourced.",
    },
    {
        "id": "entity-set-and-object-type-names",
        "topic": "how a record type becomes a URL segment",
        "basis": (
            "[not sourced] The research writes {sObjectName}, {objectTypeId} and "
            "[Organization URI] as placeholders and does not document the values."
        ),
        "value": {
            "dataverse_entity_set": "lower-cased plural by default, overridable per connector",
            "hubspot_object_type_id": "lower-cased record type by default, overridable per record",
            "salesforce": "the record type verbatim, which is the documented form",
            "base_url": "a connector field; no hostname is defaulted in",
        },
        "why": (
            "Every one of these is a deployment fact, and guessing one silently is how a "
            "deployment gets a 404 it cannot read. So each is overridable and the default is "
            "the documented convention rather than a specific org's value - and no base URL "
            "is defaulted at all, because a connector with a guessed hostname is worse than "
            "one that says it is not configured."
        ),
        "change_it": "RenderOptions.entity_sets and object_type_of in dialects.py.",
        "blast_radius": "URL segments in the rendered request, and the preview that shows them.",
    },
    {
        "id": "limits-refused-at-plan-time",
        "topic": "when a bundle that is too large is refused",
        "basis": (
            "[sourced] six documented limits. The research does not say whether a connector "
            "should check them or let the CRM refuse."
        ),
        "value": {
            "when": "before the request is rendered or sent",
            "refusal": "a 400 naming the count, the maximum, and the quoted sentence",
            "partial_request_sent": False,
        },
        "why": (
            "Letting the CRM refuse means the preview says yes and the commit says no, which "
            "is the worst of both for a rep who already read the preview. The quoted sentence "
            "is in the message so the number can be checked against the documentation rather "
            "than taken on trust."
        ),
        "change_it": "_check_limits in planner.py.",
        "blast_radius": "Plan validation. Nothing already valid changes.",
    },
    {
        "id": "sobject-collections",
        "topic": "what a collection subrequest is, and why it is supported",
        "basis": (
            "[sourced] 'Up to 5 of these subrequests can be sObject Collections or query "
            "operations'. The limit is quoted; the workflow's purpose is a related record "
            "set, so a collection is how a bundle carries more than one record of one type."
        ),
        "value": {
            "declaration": "records[].collection = {field, records: [...]}",
            "counts_as": "one subrequest, N records",
            "reference_to_it": "@{reference.field[0]} - indexed, because a collection returns a list",
            "not_supported_on": "salesforce_sobject_tree, which takes a nested tree",
        },
        "why": (
            "Without a way to express a collection, the sourced 5-of-25 rule could not be "
            "enforced or tested and a deployment adding a fourth record type of the same kind "
            "would have to send several subrequests for one step. The indexed reference is the "
            "documented '@{AccountInfo.recentItems[0].Id}' form, used because a collection's "
            "result really is a list."
        ),
        "change_it": "_collection in planner.py, and the collection branch in _render_composite.",
        "blast_radius": "The composite dialect. The tree dialect refuses one, with a reason.",
    },
    {
        "id": "commit-is-explicit",
        "topic": "what triggers a commit",
        "basis": (
            "[sourced] 'None vendor-side. Room-side this can be triggered by an event (e.g. "
            "buyer accepts pricing) or a scheduled job, but it is a single explicit request "
            "either way.'"
        ),
        "value": {
            "trigger": "one explicit POST per commit",
            "scheduler": "the host's problem, not this feature's",
            "event_trigger": "not built - nothing in the research names an event contract",
            "replay": "none; a second commit is a second commit and creates new rows",
        },
        "why": (
            "The research is clear that the vendor side is inert and the room side is a single "
            "request, so there is nothing to subscribe to and nothing to poll. Building a "
            "scheduler would be inventing a requirement the research does not mention, and "
            "an event trigger would need an event vocabulary the research does not give."
        ),
        "change_it": "There is no scheduler here to change; the commit route is the whole surface.",
        "blast_radius": "Nothing. A caller that wants a schedule calls this route from one.",
    },
    {
        "id": "vendor-response-is-not-parsed",
        "topic": "what a run records when a real CRM answered",
        "basis": (
            "[not sourced] The research quotes request shapes and limits, and no composite "
            "response body."
        ),
        "value": {
            "raw_response": "stored verbatim on the run's response_body",
            "per_subrequest_outcomes": "empty",
            "note": "attached to every run from the real transport",
        },
        "why": (
            "A composite response body is well known to people who work with these APIs, and "
            "it is not in this research. Writing a parser from memory would produce a confident "
            "answer to a question this build has not sourced - which is worse than an empty "
            "list with a note. The raw bytes are stored, so a deployment that has the "
            "documentation can read them."
        ),
        "change_it": "UrllibTransport.send and RESPONSE_NOT_PARSED_NOTE in transport.py.",
        "blast_radius": "Runs made through the real transport only. The local CRM is unaffected.",
    },
    {
        "id": "rollback-rows-are-recorded",
        "topic": "what a rolled-back subrequest looks like afterwards",
        "basis": (
            "[sourced] 'If the value is true, the entire composite request is rolled back' and "
            "'The CRM executes subrequests in order, capturing each created record id'. "
            "Together those say the ids were captured before the rollback."
        ),
        "value": {
            "outcome": "rolled_back",
            "row_deleted": True,
            "record_id_kept_on_the_run": True,
            "reason": "all_or_none",
        },
        "why": (
            "Deleting the rows and forgetting them would make 'the Account was created and "
            "then undone' indistinguishable from 'nothing happened', and those are different "
            "conversations with a rep. The row is gone from the CRM's records; the outcome is "
            "on the run."
        ),
        "change_it": "_rollback in transport.py and OUTCOME_ROLLED_BACK in vocabulary.py.",
        "blast_radius": "The run record, and the target collection after a strict failure.",
    },
    {
        "id": "no-base-url-default",
        "topic": "what a connector without a base URL does",
        "basis": (
            "[sourced] the research writes '[Organization URI]' and gives no hostnames. The "
            "storage contract forbids adding a column, and a secret is ordinary JSON."
        ),
        "value": {
            "base_url": "required for a commit, not for a preview or a declaration",
            "missing": "428 not_configured, distinct from a 400",
            "defaulted_hostname": None,
            "token": "stored verbatim; never returned by a read; the preview says has_token",
        },
        "why": (
            "A preview is useful with no CRM attached and a commit is not, so the refusal is "
            "scoped to the commit and gets its own status: a client can say 'finish the setup' "
            "rather than 'you got the request wrong'. Defaulting a hostname would turn a "
            "missing configuration into a request to somewhere unexpected."
        ),
        "change_it": "BundleNotConfigured, raised by BundleCommitter.commit in engine.py.",
        "blast_radius": "Commits on a connector with no base_url, and nothing else.",
    },
)


def describe() -> dict[str, Any]:
    """The whole registry, plus the sourced facts and gaps it is measured against."""
    return {
        "sourced_quotes": list(SOURCED_QUOTES),
        "sourced_gaps": list(SOURCED_GAPS),
        "sourced_defaults": {
            "salesforce_api_version": DEFAULT_SALESFORCE_VERSION,
            "dataverse_api_version": DEFAULT_DATAVERSE_VERSION,
        },
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
    }


def by_id(inference_id: str) -> dict[str, Any] | None:
    return next((entry for entry in INFERENCES if entry["id"] == inference_id), None)


def warning_codes() -> tuple[str, ...]:
    """Every warning code a preview can carry, with its message.

    Served alongside the inferences because a warning and an inference are the
    same kind of statement about the same gap: here is what we decided, here is
    why, and here is what would change it.
    """
    from dsr.atomic_bundle.planner import WARNINGS

    return tuple(
        {
            "code": code,
            "message": message,
            "change_it": (
                "Set collate_subrequests to false on the bundle or on the commit."
                if code == WARN_IMPLICIT_DEPENDENCY
                else "Change the bundle's dialect, policy or collate_subrequests."
            ),
        }
        for code, message in sorted(WARNINGS.items())
    )


__all__ = ["INFERENCES", "POLICY_PARTIAL", "POLICY_STRICT", "by_id", "describe", "warning_codes"]
