"""Every judgement call this build made, named so a reviewer can disagree by name.

The researched half and the inferred half are kept apart deliberately. A
reviewer who reads only this list knows exactly which sentences came from
``docs/research/digital-sales-room-workflows/wf/WF-032.md`` and which did not,
and can settle each inferred one on its own rather than taking the build as a
whole or rejecting it as a whole. Served at ``GET /api/wf-032/inferences``.

The rule every entry follows: an inference that *narrows* the research is
recorded, an inference that *widens* it is recorded more loudly, and a widening
is only accepted where the research leaves the choice genuinely open. Where it
does not - retries, rotation, an inbound endpoint - nothing was built and the
entry says so.
"""

from __future__ import annotations

from typing import Any

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "conditions-require-at-least-one-segment",
        "decision": (
            "A workflow must name at least one saved Segment in its conditions. A workflow "
            "with no conditions is refused with 422."
        ),
        "researched": (
            "Step 4 is 'Add the conditions you want to be applied to the workflow to sort "
            "out which leads you want your Workflow to send based on saved Segments from "
            "your account.' The flow includes adding conditions; it never offers a "
            "'send every lead' option."
        ),
        "alternative": (
            "Allow an empty conditions list meaning 'send every company lead'. That is the "
            "more permissive reading, and it is a real footgun: a workflow pointed at a "
            "production endpoint that fires for the whole account because a form submitted "
            "empty is the kind of bug nobody finds until it has already happened."
        ),
        "why": (
            "The research's flow makes conditions a step, not an option, and the destination "
            "URL is often a production system. Requiring the choice is also what makes the "
            "combination rule meaningful - 'any of' over zero Segments has no answer."
        ),
        "change": "POST /workflows with conditions: {segmentIds: []} -> 422 invalid_workflow.",
    },
    {
        "id": "segment-match-defaults-to-any",
        "decision": (
            "A workflow's conditions carry match='any' by default: the lead is sent when it "
            "matches at least one of its Segments. Each Segment separately carries its own "
            "rule match, also 'any' by default."
        ),
        "researched": (
            "'the conditions you want to be applied to the workflow' - plural conditions, no "
            "statement of how they combine."
        ),
        "alternative": "'all' - a lead must match every Segment listed.",
        "why": (
            "'any' is the more useful default for the use the research describes, where one "
            "workflow sends to one destination: listing three Segments is normally three ways "
            "to qualify, not a conjunction nobody would build by accident. 'all' is available "
            "per workflow and per Segment, so nothing is lost."
        ),
        "change": "conditions: {segmentIds: [...], match: 'all'}.",
    },
    {
        "id": "segment-rules-are-dotted-json-paths",
        "decision": (
            "A Segment rule is a dotted JSON path into the company lead's own data, plus an "
            "operator and a value. There is no published list of segmentable fields."
        ),
        "researched": (
            "'based on saved Segments from your account' and 'saved Segments/ICP' in the data "
            "sources. The research names no Segment field."
        ),
        "alternative": (
            "A fixed vocabulary of segmentable fields (industry, country, employees, "
            "visiting pages...). Rejected: it would make a team adding one field to a lead "
            "wait for a release, which the schema-flexibility rule forbids outright."
        ),
        "why": (
            "It is also the shape the store already resolves through its dynamic index, so a "
            "Segment is filterable with the same grammar as everything else in the product."
        ),
        "change": "rules: [{path: 'firmographics.hiringSignal', operator: 'exists'}].",
    },
    {
        "id": "required-fields-filter-contacts-not-fields",
        "decision": (
            "Keywords and required fields filter which *contacts* are sent. A contact is "
            "included when it matches at least one keyword and has every required field "
            "populated. A contact's own fields are sent in full; there is no field "
            "projection."
        ),
        "researched": (
            "'If you choose to include Contacts, you have the option of filtering the contact "
            "details on \\u2018Keywords\\u2019 and required fields.'"
        ),
        "alternative": (
            "A projection: 'Keywords' and 'required fields' choose which contact *fields* go "
            "into the payload, rather than which contacts do."
        ),
        "why": (
            "Two controls named side by side, one of which is the quoted UI label 'Keywords', "
            "read most naturally as two ways of narrowing a contact picker. The alternative "
            "would also need a fixed list of contact fields to project from, and a lead's data "
            "is arbitrary JSON by policy - so the projection would be a coordination "
            "requirement that a keyword filter is not. A team that wants fewer fields on the "
            "wire can drop them at the destination, which already has the data."
        ),
        "change": "Set contactFilter to {} to send every contact; the delivery reports how many were considered.",
    },
    {
        "id": "keywords-search-a-published-set-of-contact-fields",
        "decision": (
            "A keyword is matched case-insensitively as a substring against name, title, "
            "department, email and seniority - and the search covers list values too."
        ),
        "researched": "'filtering the contact details on \\u2018Keywords\\u2019'. No fields named.",
        "alternative": "Search every field on the contact, however a team has extended it.",
        "why": (
            "A published set makes the filter legible: an operator can see that 'security' "
            "searches titles and departments and will not search a phone number. Searching "
            "every field is friendlier but makes 'no keyword matched' un-actionable, because "
            "the operator cannot see what was searched."
        ),
        "change": "GET /api/wf-032/vocabulary -> keywordFields.",
    },
    {
        "id": "contacts-key-absent-in-company-mode",
        "decision": (
            "In payload='company' the built body has no 'contacts' key at all - not null, not "
            "an empty list."
        ),
        "researched": "'only Company for the company lead or Company + Contacts'.",
        "alternative": "Always include 'contacts', empty in company-only mode.",
        "why": (
            "A destination branching on 'if \"contacts\" in body' has to get the same answer "
            "in both modes, and a key that is present and empty is the shape that makes that "
            "branch wrong."
        ),
        "change": "GET /api/wf-032/workflows/{id}/preview with payload=company.",
    },
    {
        "id": "all-contacts-filtered-out-is-still-a-delivery",
        "decision": (
            "When keywords and required fields exclude every contact, the POST is still sent, "
            "with contacts: [] and the counts that produced it."
        ),
        "researched": (
            "The chosen output is 'Company + Contacts', and the filter is described as an "
            "option on the contact list - it filters contacts, not the send."
        ),
        "alternative": "Skip the send when no contact survives.",
        "why": (
            "The company is the lead the Segment matched, and the destination asked for "
            "Company + Contacts. Skipping would make 'no contact matched your keywords' "
            "indistinguishable from 'this company was not interesting', which is the one thing "
            "a filter must never do."
        ),
        "change": "GET /api/wf-032/deliveries -> contactsConsidered vs contactsIncluded.",
    },
    {
        "id": "token-travels-in-a-header-and-in-the-body",
        "decision": (
            "The generated token is sent on every POST twice: as the X-Albacross-Token header "
            "and as a top-level 'token' field in the body."
        ),
        "researched": (
            "'you have a token to use in your service or tool to prove that traffic is coming "
            "from the Albacross platform. It is optional to specify this automatically "
            "generated token in your system'. Where it travels is not stated."
        ),
        "alternative": "Header only, or body only.",
        "why": (
            "A header keeps the JSON body exactly the researched payload, which is what a "
            "webhook consumer normally reads. A body field is what a spreadsheet recipe with "
            "no header access can read, and Google Sheets is one of the two recipes the "
            "research names. Sending both costs a few bytes and removes the question."
        ),
        "change": "GET /api/wf-032/vocabulary -> token.header and token.bodyField.",
    },
    {
        "id": "token-is-never-in-a-list-response",
        "decision": (
            "The token appears in the create response and behind "
            "POST /workflows/{workflow_id}/token. Every other read path returns a masked hint."
        ),
        "researched": (
            "'you have a token to use in your service or tool' - the operator has to be able "
            "to read it once to configure the destination."
        ),
        "alternative": "Return the token from every read, like a password field a client can ask for.",
        "why": (
            "A secret readable out of a list response is an accident waiting to happen, and a "
            "list is exactly what a page renders without anybody deciding to show a secret."
        ),
        "change": "GET /api/wf-032/workflows -> workflows[].tokenHint is masked.",
    },
    {
        "id": "no-token-rotation",
        "decision": "There is no rotate route and no token history.",
        "researched": (
            "The token is 'automatically generated' and that is the whole of what the sources "
            "say about it. Nothing mentions rotation, expiry, or overlap."
        ),
        "alternative": (
            "A rotate route with a previous-token overlap window, as the event-stream feature "
            "in this repository has for its signing secret."
        ),
        "why": (
            "Rotation is a real need and a real security practice, but this build's brief is "
            "to land the researched decisions and not to revisit them. Inventing a rotation "
            "policy here would add a security surface the research never described, and the "
            "sibling feature's overlap semantics would be silently imported. The researched "
            "remedy for a leaked token is a new workflow."
        ),
        "change": "None. Deliberately absent.",
    },
    {
        "id": "http-scheme-allowed-and-warned",
        "decision": (
            "A destination URL must be http or https and must have a host. An http URL is "
            "accepted, and the create and patch responses carry a warning that the token will "
            "cross the wire in the clear."
        ),
        "researched": (
            "'Add a name for your Workflow and the URL you want to send data to' and 'This "
            "can be a public API for a third party tool or a custom solution.' No scheme is "
            "named."
        ),
        "alternative": (
            "Refuse http outright, as the event-stream feature does for its own vendor's "
            "documented HTTPS requirement."
        ),
        "why": (
            "That feature's rule is sourced to Dock; this research names no scheme, so "
            "refusing http would be inventing a requirement the brief explicitly tells this "
            "build not to invent. But the token exists to prove the sender's identity, and it "
            "cannot do that in clear text, so the risk is reported rather than buried. http is "
            "also the only way to point a workflow at a receiver on localhost."
        ),
        "change": "POST /workflows with an http:// url -> 201 with warnings[0] set.",
    },
    {
        "id": "no-retry-ladder-and-an-inferred-timeout",
        "decision": (
            "One attempt per delivery, a 10-second timeout, and a manual resend route. No "
            "automatic retry, no backoff, no dead-letter queue."
        ),
        "researched": (
            "Nothing. The five sources describe a destination, a payload, a token, and a "
            "once-or-updates choice. They say nothing about failure handling."
        ),
        "alternative": (
            "Copy the 26-retry ladder and the 10-second timeout that the event-stream feature "
            "in this repository implements for Dock. Tempting, and wrong: those are another "
            "ticket's researched numbers, and reusing them here would present an inference as "
            "a sourced rule."
        ),
        "why": (
            "The 10-second timeout is a judgement call and is flagged as one; the absence of a "
            "ladder is the researched position, not an omission. A delivery row still records "
            "whether a re-attempt could plausibly succeed, so an operator reading a failed row "
            "can tell 'try again' from 'this will never work' without the product deciding to "
            "try on their behalf."
        ),
        "change": "delivery.retryable, and POST /deliveries/{id}/resend.",
    },
    {
        "id": "a-failed-delivery-does-not-consume-a-once-only-lead",
        "decision": (
            "A lead counts as sent only when a POST succeeded. A failed attempt leaves the "
            "(workflow, lead) pair unsent, so the next visit tries again."
        ),
        "researched": (
            "'the workflow should only send a lead once'. The research does not say what "
            "'send' means when the destination never received anything."
        ),
        "alternative": (
            "Count the attempt: a once-only workflow then never re-sends a lead whose only "
            "delivery failed, and the lead is lost silently."
        ),
        "why": (
            "'Send a lead once' is about what the destination has seen, and a failed POST means "
            "it has seen nothing. The cost of this choice is that a permanently broken "
            "endpoint is retried on every visit, which is what a resend route and the delivery "
            "log are for."
        ),
        "change": "GET /deliveries?state=failed, then POST /deliveries/{id}/resend.",
    },
    {
        "id": "a-paused-workflow-records-a-skip",
        "decision": (
            "A visit against an inactive workflow writes a delivery row with state='skipped' "
            "and skipReason='workflow_inactive' rather than doing nothing."
        ),
        "researched": (
            "The researched flow has no pause. 'Save changes' presupposes a mutable workflow, "
            "and a workflow you cannot switch off is not something you save changes to."
        ),
        "alternative": "Do nothing, so the delivery log only ever contains attempts.",
        "why": (
            "'It was paused' and 'your Segment did not match' are two of the answers an "
            "operator most needs, and neither can be reconstructed from a row that was never "
            "written. The pause itself is the one addition to the researched vocabulary here, "
            "and it is the smallest one that makes the log complete."
        ),
        "change": "PATCH /workflows/{workflow_id} with {active: false}.",
    },
    {
        "id": "a-deleted-segment-in-use-is-refused",
        "decision": (
            "Deleting a saved Segment that a workflow still names is refused with 409. The "
            "workflow must be changed first."
        ),
        "researched": "Nothing: the research has no lifecycle for a Segment at all.",
        "alternative": (
            "Cascade the delete, or let the workflow keep a reference to a Segment that no "
            "longer exists."
        ),
        "why": (
            "A workflow pointing at a Segment that is gone would silently stop sending, and "
            "the operator would be looking at a healthy workflow and a healthy Segment list. "
            "A loud 409 is the difference between a five-minute fix and an afternoon."
        ),
        "change": "DELETE /segments/{segment_id} -> 409 delivery_conflict.",
    },
    {
        "id": "a-workflow-scope-is-its-segments",
        "decision": (
            "A workflow's conditions may name Segments, and optionally narrow them to one room. "
            "There is no other condition type."
        ),
        "researched": (
            "'Add the conditions you want to be applied to the workflow to sort out which "
            "leads you want your Workflow to send based on saved Segments from your account.'"
        ),
        "alternative": (
            "Also allow ad-hoc conditions on the workflow itself, so a workflow can be 'that "
            "Segment, but only rooms in Europe' without editing the Segment."
        ),
        "why": (
            "The room narrowing is there because the brief requires room-scoped paths to stay "
            "room-scoped, and a Segmented visit has to be attributable to a room for that to "
            "mean anything. Ad-hoc workflow conditions were left out rather than added: the "
            "researched vocabulary for a condition is a Segment, and a second kind would be a "
            "second thing to argue about."
        ),
        "change": "POST /workflows with roomId, or conditions: {segmentIds: [...], roomId}.",
    },
    {
        "id": "the-visit-is-recorded-first",
        "decision": (
            "A visit is written before any workflow is evaluated, and its record carries the "
            "matched-company count. The visit is the researched trigger - 'no user action' - "
            "so it exists whether or not anything matches it."
        ),
        "researched": (
            "'Trigger is the segment-matching company visit - no user action.' The research "
            "has no inbound endpoint, because 'No public inbound REST reference for Albacross "
            "was reachable in the sources read.'"
        ),
        "alternative": "Evaluate first and only record a visit that matched something.",
        "why": (
            "A visit that matched nothing is the state an operator needs to see: it is how a "
            "Segment gets debugged. Recording only matches makes 'why is this company not in "
            "my Segment' unanswerable from the product."
        ),
        "change": "GET /api/wf-032/visits?matched=false.",
    },
    {
        "id": "collections-are-prefixed",
        "decision": (
            "Every collection this feature owns is prefixed wf032_."
        ),
        "researched": "Nothing: collection names are this product's.",
        "alternative": "Plain names, as most collections in this repository use.",
        "why": (
            "The store is one flat namespace and a hundred features are landing at once, so an "
            "unprefixed 'contact' or 'segment' is a coin flip against whoever writes it next. "
            "The prefix costs nothing and makes the collision impossible by construction."
        ),
        "change": "GET /api/wf-032/vocabulary -> collections.",
    },
    {
        "id": "lead-state-is-one-row-per-pair",
        "decision": (
            "The once-versus-updates decision is backed by one row per (workflow, company "
            "lead) pair, at a deterministic id."
        ),
        "researched": (
            "'only send a lead once' / 'receive the same lead with updated activity data if "
            "that lead visits your webpage again'."
        ),
        "alternative": "Derive the decision by scanning the delivery log on every visit.",
        "why": (
            "A scan cannot be indexed on a pair of ids and would be re-read on every visit, "
            "which is the hot path of the whole workflow. A deterministic id also makes the "
            "invariant checkable: create once, then only ever update."
        ),
        "change": "GET /api/wf-032/collections is not exposed; the rows are visible in /api/audit.",
    },
    {
        "id": "no-inbound-verification-endpoint",
        "decision": "Nothing accepts a token from a caller, and nothing verifies one.",
        "researched": (
            "'No public inbound REST reference for Albacross was reachable in the sources "
            "read.' Verification is 'in **your** service or tool'."
        ),
        "alternative": "A POST /workflows/{id}/token/verify endpoint a destination could call.",
        "why": (
            "The destination already holds the token, so a verify endpoint would tell it "
            "nothing it could not learn by comparing two strings itself - and it would add an "
            "unauthenticated inbound surface the research does not describe. The comparison "
            "the destination should make is published as a runnable recipe instead."
        ),
        "change": "GET /api/wf-032/vocabulary -> token.verificationRecipe.",
    },
)


def describe() -> dict[str, Any]:
    """The whole list, plus a count, for ``GET /api/wf-032/inferences``."""
    return {"count": len(INFERENCES), "inferences": [dict(entry) for entry in INFERENCES]}
