"""Every judgement call this package makes, in one inspectable place.

The research for WF-044 is unusually specific about the *editor* on the CRM side:
it names the panel, the method dropdown, the HTTPS rule, three authentication
types, two body modes, Save, Publish, the Test control, and the two permissions.
What it does not do is say how the *room's receiving end* behaves on the edges of
that description, and the edges are where a build has to decide something.

Those decisions are collected here rather than left as comments in function
bodies, because a judgement call in a comment is one nobody re-reads and a wrong
one becomes product behaviour without anyone noticing. Each entry is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``GET /api/wf-044/inferences``, so a
  reviewer reads the list instead of reconstructing it from a diff.

The first entry is not an inference but a fact this build had to import from
outside its three sources, and it is listed first because it is the largest thing
here: the research names the *capability* (request-signature verification) and
says not to invent a scheme, but does not publish the scheme itself.

One entry is a boundary rather than a judgement call, and is listed for that
reason: ``not-built`` records what this build deliberately does not do, because a
feature whose page does not show its own edges overstates itself.
"""

from __future__ import annotations

from typing import Any

from dsr.crm_outbound_webhooks.vocabulary import (
    API_KEY_LOCATIONS,
    AUTH_MODES,
    BODY_MODES,
    CONTRACT_VERSION,
    METHODS,
    OBJECTS,
    OUTCOMES,
    PERMISSIONS,
    PUBLISH_PERMISSIONS,
    SETUP_PERMISSIONS,
    SUBSCRIPTION_LIMIT_PER_APP,
    default_id_key,
    default_stage_key,
)

#: The sentence from the research that governs most of the surface below.
SOURCED_QUOTE = (
    "To use a request signature in your webhook header: Click the **Authentication type** "
    "dropdown menu. Then, select **Include request signature in header**. Then, enter your "
    "HubSpot App ID."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "hubspot-request-signature-scheme",
        "topic": "the exact bytes a request signature covers",
        "topic_note": "the one thing here that this workflow's three sources do not publish",
        "basis": (
            "The research names the capability and points at it rather than at a scheme of "
            "our own: 'Because the room can verify the request signature, it does not need a "
            "per-workflow secret. Request-signature verification is a documented HubSpot "
            "capability the room can lean on instead of inventing its own HMAC scheme.' It "
            "does not give the canonical string, the header names, or a replay window - "
            "those live on the vendor's webhook-signature documentation, not on the three "
            "pages this workflow cites."
        ),
        "value": {
            "algorithm": "HMAC-SHA256, base64",
            "canonical_string": "METHOD \\n URI \\n BODY \\n TIMESTAMP",
            "signature_header": "x-hubspot-signature-v3",
            "timestamp_header": "x-hubspot-request-timestamp",
            "timestamp_unit": "milliseconds since the epoch",
            "replay_window_seconds": 300,
            "get_body": "empty - a GET has no body, and its properties are in the query string",
            "also_accepted": "the same digest hex encoded",
        },
        "why": (
            "The sources' direction is explicit: lean on the vendor's scheme, do not invent "
            "one. So this uses the vendor's published v3 shape rather than a signature of "
            "our own design - which is also the only choice that makes a real CRM-side "
            "action work against this endpoint on the day it is pointed at it. Two "
            "deliberate hedges: the comparison is a signed distance in either direction, "
            "because a sender whose clock runs fast is as legitimate as one whose clock "
            "runs slow and refusing it is a bug dressed as a security control; and a hex "
            "digest is accepted alongside the base64 one, because being strict about "
            "encoding in the one place a secret is involved costs a working integration "
            "and buys nothing - both forms are unforgeable without the secret."
        ),
        "change_it": (
            "canonical_string(), sign() and _verify_signature() in "
            "dsr/crm_outbound_webhooks/signing.py, and the header names in vocabulary.py."
        ),
        "blast_radius": "Which requests authenticate, and every signature-authenticated endpoint.",
    },
    {
        "id": "super-admin-does-not-imply-publish",
        "topic": "whether a Super Admin may publish",
        "topic_note": "the permission reading a reviewer will check first",
        "basis": (
            "Sourced, exactly: 'To set up webhook actions in workflows, users must have Edit "
            "permissions for workflows or Super Admin permissions. To publish workflows, "
            "users must have Publish permissions for workflows.' The first sentence offers "
            "Super Admin as an alternative. The second names one permission and does not "
            "mention Super Admin at all."
        ),
        "value": {
            "setup": list(SETUP_PERMISSIONS),
            "publish": list(PUBLISH_PERMISSIONS),
            "super_admin_publishes": False,
        },
        "why": (
            "Failing closed on the sentence as written. Super Admin implying every "
            "permission is the conventional reading of the tier name, but this build applies "
            "the source rather than the convention - and the source is unusually precise "
            "here, having already written the 'or Super Admin' alternative out once and "
            "then not writing it again. The cost of being wrong is one explicit permission "
            "on a query string; the cost of assuming is an endpoint published by someone the "
            "vendor's own rule would not have published."
        ),
        "change_it": "SETUP_PERMISSIONS and PUBLISH_PERMISSIONS in vocabulary.py.",
        "blast_radius": "Who may publish, and therefore which endpoints go live.",
    },
    {
        "id": "published-endpoints-are-amendable",
        "topic": "whether a published endpoint can be edited in place",
        "basis": (
            "The research describes editing a CRM-side workflow and then publishing it, and "
            "describes no separate unpublish step on the room's side. It says nothing about "
            "amending a room endpoint that is already live."
        ),
        "value": {"amendable": True, "off_switch": "unpublish", "retirement": "not applicable"},
        "why": (
            "The stricter reading - refuse, unpublish, fix, republish - has no sourced basis "
            "and one concrete cost: a rep who mistyped a URL on a live integration cannot fix "
            "it without taking the integration down first, and the thing they are editing is "
            "their own configuration. Every amendment is already an audit row naming the "
            "route that made it, so the change is visible to anyone who looks for it. "
            "Unpublishing remains the off switch, and it is a separate route precisely so "
            "that stopping the traffic is a deliberate act."
        ),
        "change_it": "The status check in engine.amend().",
        "blast_radius": "Whether a PATCH to a live endpoint is accepted.",
    },
    {
        "id": "unauthenticated-deliveries-write-nothing",
        "topic": "whether a request that failed authentication leaves a row",
        "topic_note": "the asymmetry that looks like an oversight until it is read",
        "basis": (
            "The data flow says 'room authenticates (signature / bearer) -> maps payload onto "
            "the room model'. It does not say what a failed authentication records, and this "
            "workflow's sources say nothing about hostile traffic."
        ),
        "value": {
            "unauthenticated": "401, and not one row written",
            "no_endpoint": "409, and not one row written",
            "everything_after_authentication": "recorded, whatever it does",
        },
        "why": (
            "A request that failed authentication is the one input to this product that has "
            "not been shown to be entitled to anything, and a row it leaves behind is "
            "indistinguishable from a real one to everyone who reads the log afterwards - "
            "which is worse than losing the row, because it makes the log untrustworthy in "
            "the exact place it is meant to be strongest. Every refusal *after* "
            "authentication is recorded, because there the sender has proved itself and a rep "
            "whose automation reached a room and was turned away needs to see that in the "
            "log rather than in a support ticket."
        ),
        "change_it": "The `if not ok: raise` branch in engine.receive().",
        "blast_radius": "What a 401 leaves behind, and the size of the delivery log.",
    },
    {
        "id": "retry-is-a-counter-not-a-row",
        "topic": "what a retried delivery does",
        "basis": (
            "Sourced that retries happen: 'When a webhook is slow or times out, the workflow "
            "action may take longer than expected to execute.' Not sourced: any idempotency "
            "rule for this workflow's own endpoint."
        ),
        "value": {
            "key": "the payload's static delivery_id when present, else the properties",
            "repeat": "answered 200, counted on the delivery that was kept",
            "second_row": False,
        },
        "why": (
            "A timeout is indistinguishable from a failure to the sender, so a CRM that "
            "retries is behaving normally and a log holding the same stage change three "
            "times is a log nobody reads. Counting on the kept row keeps the retry visible "
            "without inflating the delivery count or re-notifying the rep. The static "
            "``delivery_id`` key is not an invention: **Customize request body** is "
            "documented as the way to 'add a static field', so a rep can make their retries "
            "unambiguous with a key they type into the CRM."
        ),
        "change_it": "_find_kept(), _record_duplicate() and _fingerprint() in engine.py.",
        "blast_radius": "The delivery count, the duplicate counter, and whether a rep is told twice.",
    },
    {
        "id": "resolve-then-create-the-deal",
        "topic": "what happens to a deal this room has never seen",
        "basis": (
            "'The room's endpoint verifies the signature, resolves the record, and updates the "
            "buyer's room state.' The research does not say whether resolution can fail, or "
            "what a room that has not yet synced a deal does with a signed delivery about one."
        ),
        "value": {
            "external_id_matches": "that deal",
            "no_external_id_single_deal_room": "the room's only deal",
            "external_id_matches_nothing": "a new deal is created for that id",
            "no_external_id_several_deals": "refused 409, deal_unresolved",
        },
        "why": (
            "The endpoint is authenticated and room-scoped, so a signed delivery from it is "
            "how a room learns about a deal it has not met - refusing that would mean a room "
            "can never show a stage change for a deal created in the CRM after the room was "
            "set up, which is the ordinary case. The ambiguity that is *not* resolvable is "
            "refused rather than guessed at: picking one of several deals out of a payload "
            "with no id is how a stage lands on the wrong buyer's panel, and the room says so "
            "in the log instead of quietly choosing."
        ),
        "change_it": "_resolve() in engine.py.",
        "blast_radius": "Which deal a delivery updates, and whether it creates one.",
    },
    {
        "id": "omitted-properties-are-stale-not-deleted",
        "topic": "what a property missing from a payload does to the deal panel",
        "basis": (
            "'**Include all [object] properties**' and '**Customize request body**' both exist, "
            "and the customised one is documented as sending only specific properties. So a "
            "key absent from a payload means two different things depending on the mode, and "
            "the research does not say which behaviour either mode wants."
        ),
        "value": {
            "merge": "incoming properties overwrite, nothing is removed",
            "absent_key_reported_as": "stale_keys",
            "deleted": False,
        },
        "why": (
            "A customised body sends only the keys it names, so clearing everything else would "
            "erase the room's own data every time a rep trimmed their CRM-side body - a "
            "silent data loss in exchange for tidiness. Under **Include all** an absent key "
            "genuinely does mean the property was cleared in the CRM, and the honest report "
            "for that is 'this panel value is no longer being confirmed' rather than a value "
            "the room invented. Reporting it as stale puts the question to the reader instead "
            "of answering it wrongly."
        ),
        "change_it": "The `stale` computation and the merge in engine._apply().",
        "blast_radius": "What the deal panel shows, and what stale_keys reports.",
    },
    {
        "id": "quiet-when-nothing-changed",
        "topic": "when a delivery notifies the rep",
        "basis": (
            "'Updates deal panel and notifies the rep.' The trigger the research describes is "
            "a change - 'deal stage becomes \"Contract Sent\"' - so the sender's own condition "
            "already implies something moved. What happens when it arrives and nothing did is "
            "not described."
        ),
        "value": {
            "notice_when": ["stage_changed", "properties_only"],
            "no_notice_when": ["stage_unchanged", "none"],
            "delivery_row": "always written",
        },
        "why": (
            "A notification whose text is the same as the last one is a notification people "
            "mute, and then the one that matters is muted too. The delivery itself is always "
            "recorded, because the log is the audit trail and a delivery that changed nothing "
            "is still a delivery that arrived. The rule is mechanical - notify when the panel "
            "changed - rather than per object type, so there is no branch to be surprised by."
        ),
        "change_it": "The `noticed` computation in engine._deliver().",
        "blast_radius": "How many notices exist, and what a rep is told.",
    },
    {
        "id": "get-carries-the-same-contract",
        "topic": "what a GET delivery's properties are",
        "topic_note": "sourced method, unsourced placement",
        "basis": (
            "Sourced: 'You can send both POST and GET requests using workflows.' Not sourced: "
            "where a GET's properties go, which follows from GET having no body and is not "
            "stated anywhere in the sources."
        ),
        "value": {
            "properties_from": "the query string",
            "signed_body": "empty",
            "contract": "the same versioned one a POST is read against",
        },
        "why": (
            "A method the vendor supports is a method a rep can pick in the same dropdown, so "
            "refusing it would break a configuration the researched tool allows. Query string "
            "is the only place a GET can carry data, and signing an empty body is what makes "
            "the signature still cover everything the request said - the URI, which includes "
            "the query string, is signed as a whole."
        ),
        "change_it": "The `is_get` branches in engine.receive() and engine._deliver().",
        "blast_radius": "Whether a GET delivery authenticates and what it is read from.",
    },
    {
        "id": "one-endpoint-per-room-many-automations",
        "topic": "why a second endpoint per room is refused",
        "basis": (
            "Sourced, and the extensibility line is the whole design: 'The room exposes one "
            "inbound endpoint per tenant with a versioned payload contract, so any number of "
            "CRM-side automations can target it. Because the room can verify the request "
            "signature, it does not need a per-workflow secret.'"
        ),
        "value": {
            "endpoints_per_room": 1,
            "automations_per_endpoint": "unbounded, up to the app's 1,000",
            "per_automation_secret": False,
            "unknown_automation_label": "accepted and reported, not refused",
        },
        "why": (
            "The refusal is the researched extensibility made load-bearing: a second endpoint "
            "would need its own secret, and a per-workflow secret is exactly what the source "
            "says the room does not need. The same reasoning runs the other way at delivery "
            "time - a payload naming an automation this room has not registered is *accepted* "
            "and reported as ``automation_unknown``, because an allow-list of automations "
            "would mean every new CRM-side workflow needed the room's cooperation before it "
            "could report anything."
        ),
        "change_it": "The EndpointExists check in engine.register(), and the notes list in _deliver().",
        "blast_radius": "How many endpoints a room has, and which automations it will accept.",
    },
    {
        "id": "no-inbound-quota",
        "topic": "whether this endpoint rate limits the CRM",
        "basis": (
            "Sourced twice, and both halves matter: 'Webhook calls made via workflows do not "
            "count towards the API rate limit', and 'HubSpot regulates webhook traffic "
            "separately from other workflow processes ... When a webhook is slow or times "
            "out, the workflow action may take longer than expected to execute.'"
        ),
        "value": {
            "inbound_quota": None,
            "counts_for_observability": True,
            "per_delivery_transactions": 1,
            "outbound_calls": 0,
        },
        "why": (
            "The first quote says a rate limit here would be a limit the sources explicitly "
            "exclude, so there is none; deliveries are still counted, because observability is "
            "not enforcement. The second quote sets the obligation that does apply: be fast, "
            "because a slow endpoint makes the *CRM's* workflow action slow. That is why a "
            "delivery is exactly one transaction with no socket in it - the whole design is "
            "shaped by how long the sender waits."
        ),
        "change_it": "The single transaction in engine._deliver(); the notes in engine.summary().",
        "blast_radius": "How long the CRM waits, and what a delivery costs.",
    },
    {
        "id": "the-room-names-two-properties",
        "topic": "which payload property is the stage, and which is the record id",
        "basis": (
            "The research quotes 'deal stage becomes \"Contract Sent\"' and 'Include all "
            "[object] properties', but publishes no field names - and it cannot, because the "
            "body's keys are whatever the rep typed into **Customize request body**."
        ),
        "value": {
            "configured_on": "the endpoint record",
            "id_key_default": {obj: default_id_key(obj) for obj in OBJECTS},
            "stage_key_default": {obj: default_stage_key(obj) for obj in OBJECTS},
            "other_properties": "kept verbatim, never renamed",
        },
        "why": (
            "Hard-coding a vendor's field names would be this build guessing at a field map "
            "the research says the rep chooses. Naming the two keys the room *acts on* on the "
            "endpoint record means a team whose CRM calls them something else configures them "
            "once, and every other property still lands untouched - which is the "
            "schema-flexibility rule applied to this package's own reader rather than an "
            "exception carved out of it."
        ),
        "change_it": "id_key and stage_key on the endpoint record; default_id_key/default_stage_key.",
        "blast_radius": "Which property a room reads the stage from, and the record id from.",
    },
    {
        "id": "not-built",
        "topic": "what this build deliberately does not do",
        "basis": (
            "The research's gaps are explicit: 'Salesforce outbound messages and Flow HTTP "
            "callout could not be sourced ... Microsoft Dataverse's outbound webhook mechanism "
            "is likewise unsourced'. The Salesforce side is 'documented location known but "
            "page not readable; not claimed'."
        ),
        "value": {
            "outbound_delivery": "not built; this is the receiving end of the researched flow",
            "crm_api_calls": "not built; nothing here opens a socket",
            "salesforce_outbound_messages": "unsourced, so not modelled",
            "dataverse_service_bus": "unsourced, so not modelled",
            "the_crm_test_button": "a CRM-side control; the room shows a preview instead",
            "unknown_payload_keys": "kept, not dropped",
        },
        "why": (
            "A function that opens a socket to a vendor API this product has no credentials "
            "for is not a feature; it is a function that would fail in production and pass "
            "review. The two unsourced vendors are left unmodelled rather than guessed at, "
            "because a workflow marked unsourced in this project is a hypothesis and not a "
            "specification. The Test control is a CRM-side button, so the room publishes the "
            "bytes it expects instead of pretending to press it."
        ),
        "change_it": "Nothing to change: this entry is the record of the boundary.",
        "blast_radius": "What the room claims to be, and what it claims not to be.",
    },
)


def by_id(identifier: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == identifier:
            return entry
    return None


def describe() -> dict[str, Any]:
    """The whole register, served.

    The sourced half comes back beside the inferred half, because the point of the
    endpoint is to see where the line falls.
    """
    return {
        "sourced_quote": SOURCED_QUOTE,
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "sourced": {
            "methods": list(METHODS),
            "url_rule": "Webhook URLs are restricted to a secure protocol and must begin with HTTPS.",
            "auth_modes": list(AUTH_MODES),
            "api_key_locations": list(API_KEY_LOCATIONS),
            "body_modes": list(BODY_MODES),
            "objects": list(OBJECTS),
            "permissions": list(PERMISSIONS),
            "publish_rule": "Workflows must be **published** to go live.",
            "subscription_limit_per_app": SUBSCRIPTION_LIMIT_PER_APP,
            "contract_version": CONTRACT_VERSION,
            "outcomes": list(OUTCOMES),
        },
    }
