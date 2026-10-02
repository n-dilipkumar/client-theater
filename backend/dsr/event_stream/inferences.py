"""Every judgement call WF-025's build rests on, in one inspectable place.

A judgement call left as a comment in a function body is one nobody re-reads,
and a wrong one becomes product behaviour without anyone noticing. Collected
here, each entry is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``/api/wf-025/inferences`, so a
  reviewer or a client can read the whole list instead of inferring it from a
  diff.

Nothing here is a migration, a typed column, or a new required field. It is a
list of ordinary JSON, like everything else this project stores, and it is a
*record* of a judgement rather than a mechanism that enforces one.

The line the research draws
---------------------------
The research is unusually specific about the payload and unusually silent about
everything around it. It quotes the ``webhook-event`` field names, the
``associatedObjects`` kinds, the subscription types, the anonymous-user rule, the
one-hour presigned expiry, the admin requirement, the 10-second timeout, and a
26-retry ladder. It says **nothing** about our HTTP routes, our collection
names, our header names, our delivery states, or our secret storage. Those are
this build's, and they are below.
"""

from __future__ import annotations

from typing import Any

from dsr.event_stream.vocabulary import (
    EVENT_TYPES,
    MAX_RETRIES,
    PULL_RESOURCES,
    RETRY_LADDER,
    SEISMIC_EVENTS,
    WEBHOOK_TIMEOUT_SECONDS,
)

#: The two sentences that govern the delivery policy, quoted.
SOURCED_QUOTE = (
    '"Seismic sends a `x-seismic-signature` header with each webhook request. This header '
    'contains a HMAC signature of the request body." / "Total number of retries: 26" / '
    '"Seismic will wait for 10 seconds for the webhook to respond."'
)

#: The sentence that governs the subscription set, quoted. The word that decides
#: whether an unknown event type is a refusal.
SUBSCRIPTION_TYPES_QUOTE = (
    '"subscription types include `workspace.created`, `workspace.viewed`, '
    '`workspace.page.viewed`, ... `course.completed|course.reviewed`."'
)


INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "event-type-set-is-a-floor",
        "topic": "whether a subscription type outside the published list is accepted",
        "topic_note": "the inference most likely to be disagreed with",
        "basis": (
            "The research introduces the list with 'subscription types **include**', not 'are' "
            f"exactly: {SUBSCRIPTION_TYPES_QUOTE} It is a list this build read, not a promise about "
            "what the vendor will ever publish."
        ),
        "value": {
            "unknown_types": "accepted, stored, and reported as unknown_types on the subscription",
            "empty_or_non_string": "refused with 400",
            "where_reported": [
                "GET /subscriptions",
                "GET /webhooks/{id}/subscriptions",
                "GET /vocabulary",
            ],
        },
        "why": (
            "A closed enum is the tidier product and it is what most of this repository's other "
            "vocabularies do. It is wrong here: refusing a type the vendor published after this "
            "build would break every subscription pointing at it, and the research's own wording "
            "does not license the refusal. The empty-and-non-string cases are still refused, which "
            "is where the real typo risk is - a picker in the UI cannot produce a misspelt type."
        ),
        "change_it": (
            "require_event in dsr/event_stream/vocabulary.py. Add a membership test against "
            "KNOWN_EVENTS and raise VocabularyError. No route changes."
        ),
        "blast_radius": "Which subscriptions can be created, and whether a vendor's new type works.",
    },
    {
        "id": "admin-only-creates-a-webhook",
        "topic": "which operations require the account admin role",
        "basis": (
            'The research quotes exactly one permission sentence: "You must be an account `admin` '
            'to create a webhook." It says nothing about viewing, pausing, or unsubscribing, even '
            "though the user flow has an admin doing all of them from Settings -> Webhooks."
        ),
        "value": {
            "POST /webhooks": "requires role=admin; absent role is refused as firmly as a wrong one",
            "every_other_route": "no role required",
        },
        "why": (
            "Applying the rule to the routes the research does not name would be inventing a "
            "permission model, and in a direction that makes the feature unusable for anyone the "
            "research did not discuss. The quoted rule is enforced on the quoted operation and no "
            "further. Absent-is-refused rather than absent-is-admin, because a permission rule that "
            "cannot be seen failing is not a rule."
        ),
        "change_it": (
            "EventStream.require_admin in dsr/event_stream/stream.py, and the role parameter on the "
            "create-webhook route."
        ),
        "blast_radius": "Who can create a webhook. Nothing else.",
    },
    {
        "id": "https-only-targets",
        "topic": "which subscriber URLs are accepted",
        "basis": (
            'The user flow says "enter the HTTPS target URL". No scheme list, no rule about '
            "credentials, fragments, or private addresses."
        ),
        "value": {
            "scheme": "https only",
            "credentials_in_url": "refused",
            "fragment": "refused",
            "private_and_loopback_hosts": "refused unless allow_private_target is set",
            "allow_private_target": "off by default, and set by the seeder for the demo",
        },
        "why": (
            "A webhook ships customer page metadata, so clear text is not shippable and the "
            "research says https. Credentials in a URL land in every subscriber's access log with "
            "no way to rotate them there. A fragment is never sent to a server, so a target with one "
            "could never pass verification. The private-host rule is not sourced and is the one "
            "entry here most likely to be unwanted in a deployment that legitimately points at "
            "localhost - which is why it is switchable rather than absolute."
        ),
        "change_it": "validate_target_url in dsr/event_stream/targets.py, including BLOCKED_HOSTNAMES.",
        "blast_radius": "Which webhooks can be created.",
    },
    {
        "id": "unverified-target-is-not-created",
        "topic": "what happens when the verification POST fails",
        "basis": 'The research says Dock "verifies the URL with a POST" at creation, and does not say what happens when it fails.',
        "value": {
            "on_failure": "no record is written; the request is refused with 400 target_not_verified",
            "on_success": "verified_at and verified_status are stored on the webhook",
        },
        "why": (
            "Creating the webhook anyway would fill the list with endpoints that have never worked, "
            "which is a list nobody cleans up. The refusal carries the endpoint's own status and "
            "body, because the operator is entitled to see what their endpoint said. Note the "
            "asymmetry with a failed *delivery*: that is recorded, because the event happened and "
            "somebody will come looking for it."
        ),
        "change_it": "EventStream.verify_target and create_webhook in dsr/event_stream/stream.py.",
        "blast_radius": "Whether a broken target can be saved and forgotten.",
    },
    {
        "id": "verification-is-a-test-event",
        "topic": "what payload the verification POST carries",
        "basis": (
            "The research names two separate affordances - the verifying POST at creation, and "
            '"**Send test events**" during setup - and describes neither payload.'
        ),
        "value": {
            "payload": "a well-formed webhook-event, marked test: true",
            "event_field": "the real event type being tested, or the literal 'test' for verification",
            "user_object": "absent, because a verification POST has no user",
        },
        "why": (
            "One mechanism covering two researched steps is better than a second, undocumented "
            "handshake. Marking it matters: a payload indistinguishable from a real event would put "
            "a fake view into somebody's warehouse, and the test flag is the only thing that stops "
            "that. The absent user is the researched anonymous rule doing its job - a test event "
            "demonstrates it."
        ),
        "change_it": "EventStream.test_payload in dsr/event_stream/stream.py.",
        "blast_radius": "What a subscriber receives on setup, and whether it can filter it out.",
    },
    {
        "id": "subscription-type-payloads-inferred",
        "topic": "which associated object each event type must carry",
        "basis": (
            "The research names the eight object kinds and pairs some of them with event types by "
            "name. It does not say what an order form, an NDA, or a course completion carries."
        ),
        "value": {
            "sourced": [
                "workspace.page.viewed -> workspacePage",
                "workspace.section_navigation.clicked -> workspaceSection",
                "workspace.file.viewed|downloaded -> file",
                "workspace.form.submitted -> workspaceForm",
                "course.completed|reviewed -> workspacePlanTask",
            ],
            "inferred": [
                "workspace.order_form.* -> file (the order form is a document the buyer opens, "
                "downloads, and signs)",
                "workspace.NDA.signed -> file (the NDA is a document that is signed)",
            ],
            "always": ["workspace", "account"],
            "optional": ["user"],
        },
        "why": (
            "A subscriber that can rely on a key being present is worth more than a payload that is "
            "merely possible, and the research's own list of object kinds exists so those keys can "
            "be relied on. The two inferred rows are the weakest part of this entry and are marked "
            "so a reviewer can disagree with exactly them."
        ),
        "change_it": "EVENT_OBJECTS in dsr/event_stream/vocabulary.py.",
        "blast_radius": "Which events are accepted, and what a subscriber can read off the payload.",
    },
    {
        "id": "asset-snapshot-is-top-level",
        "topic": "where the asset snapshot lives in an asset.* payload",
        "basis": (
            "The research says `asset.*` payloads include `trackingEnabled` and that the payload "
            "'embeds the asset snapshot'. It does not list `asset` among the eight "
            "`associatedObjects` kinds."
        ),
        "value": {"location": "payload.asset", "not": "payload.associatedObjects.asset"},
        "why": (
            "Putting it in associatedObjects would invent a ninth object kind the research does not "
            "name, and the payload builder refuses unknown kinds precisely so a caller cannot "
            "quietly invent one. `trackingEnabled` is required rather than optional: it is what "
            "distinguishes a gated share link from an ordinary view, and a payload without it cannot "
            "be attributed to a tracked link at all."
        ),
        "change_it": "build_event_payload and normalise_asset_snapshot in dsr/event_stream/payloads.py.",
        "blast_radius": "The shape of every asset.* delivery.",
    },
    {
        "id": "presentation-shared-is-also-share-link-scoped",
        "topic": "whether presentation.shared needs a share link",
        "basis": (
            'The evidence sentence names two types: "`presentation.viewed` and '
            '`presentation.downloaded` are emitted for presentation share-link activity only." '
            "The type list also publishes `presentation.shared`."
        ),
        "value": {"all_three": "require a shareLink", "refused_without_one": True},
        "why": (
            "The sentence is about the share link being the only *source* of presentation activity, "
            "not about two of the three verbs. Treating presentation.shared differently would mean a "
            "payload that the research says cannot exist."
        ),
        "change_it": "SHARE_LINK_EVENTS in dsr/event_stream/vocabulary.py.",
        "blast_radius": "Whether a presentation.shared event can be recorded at all.",
    },
    {
        "id": "share-link-field-name",
        "topic": "the field name a share link travels in",
        "basis": "The research says the activity comes from a share link and never names the field.",
        "value": {
            "field": "shareLink",
            "required_for": [
                "presentation.viewed",
                "presentation.downloaded",
                "presentation.shared",
            ],
        },
        "why": (
            "A rule that cannot be expressed cannot be enforced, so the field had to be named. "
            "camelCase matches the researched `associatedObjects` spelling rather than this "
            "product's own snake_case, because the payload is the vendor's contract."
        ),
        "change_it": "SHARE_LINK_FIELD in dsr/event_stream/payloads.py.",
        "blast_radius": "The one field name a subscriber has to read to use presentation events.",
    },
    {
        "id": "property-previous-value-is-omitted",
        "topic": "what happens when there is no previous value",
        "basis": (
            "The research lists `propertyName` / `propertyPreviousValue` / `propertyValue` on the "
            "payload. It does not say the middle one is always present."
        ),
        "value": {
            "when_absent": "the key is omitted from the payload",
            "never": '"propertyPreviousValue": null',
        },
        "why": (
            "A view has no previous state. Sending null would claim a value that does not exist, "
            "and a subscriber diffing two payloads would see a change from nothing to something."
        ),
        "change_it": "build_event_payload in dsr/event_stream/payloads.py.",
        "blast_radius": "One key, present or absent, on every payload.",
    },
    {
        "id": "property-name-defaults-to-the-event",
        "topic": "what propertyName holds when the caller does not name one",
        "basis": "The research says the field is on the payload and never says what it contains for a view.",
        "value": {
            "default": "the event's own family plus '.activity', e.g. workspace.activity",
            "override": "propertyName on the record_event call",
        },
        "why": (
            "The field is researched as present, so omitting it entirely would break a subscriber "
            "that reads it. Inventing a specific property path per event type would be inventing a "
            "schema; deriving it from the event's own name is the smallest thing that is always "
            "true."
        ),
        "change_it": "_default_property_name in dsr/event_stream/payloads.py.",
        "blast_radius": "The value of one payload field when the caller is silent.",
    },
    {
        "id": "associated-object-url",
        "topic": "which associated objects carry a url",
        "basis": (
            "The research's example object carries a `url` into the vendor's API, and its pull "
            "list publishes item paths for workspaces only. It publishes no pull route for account, "
            "user, file, or workspaceForm in this workflow."
        ),
        "value": {
            "with_url": {
                "workspace": "/api/wf-025/backfill/workspaces/{id}",
                "workspacePlanTask": "/api/wf-025/backfill/workspace-plan-tasks",
            },
            "without_url": [
                "account",
                "user",
                "file",
                "workspaceForm",
                "workspacePage",
                "workspaceSection",
            ],
            "missing_url_means": "the key is absent, not null",
        },
        "why": (
            "A url is a promise that GETting it works. Emitting one for an object kind this product "
            "does not serve would be a link that 404s in somebody's integration, and the failure "
            "would surface as their problem, days later. Omission is honest and is distinguishable "
            "from a null url."
        ),
        "change_it": "ASSOCIATED_OBJECT_URLS in dsr/event_stream/vocabulary.py.",
        "blast_radius": "Which payload keys can be followed.",
    },
    {
        "id": "delivery-states",
        "topic": "the states a delivery row can be in",
        "basis": "The research documents a retry ladder and a secret. It documents no state machine.",
        "value": {
            "states": ["delivered", "retrying", "failed", "skipped", "tested"],
            "skipped_reasons": ["webhook_paused", "subscription_paused", "filter_excluded"],
        },
        "why": (
            "A rep needs to tell 'your endpoint refused it' from 'it was paused on purpose' from "
            "'your filter excluded it', and all three look identical if the only state is success or "
            "failure. The three skip reasons are the researched pause plus the one filter rule."
        ),
        "change_it": "DELIVERY_STATES and the SKIPPED reasons in dsr/event_stream/delivery.py and stream.py.",
        "blast_radius": "Every delivery filter, and the summary tiles.",
    },
    {
        "id": "retry-ladder-is-a-schedule",
        "topic": "whether a failed delivery is retried inside the request",
        "basis": f"{SOURCED_QUOTE} The ladder is '1 min, then 10 min, then hourly x24' - {MAX_RETRIES} steps, matching the documented total.",
        "value": {
            "attempt_per_call": 1,
            "ladder_seconds": list(RETRY_LADDER),
            "max_retries": MAX_RETRIES,
            "max_attempts": MAX_RETRIES + 1,
            "timeout_seconds": WEBHOOK_TIMEOUT_SECONDS,
            "driven_by": "POST /deliveries/{id}/retry, or a queue worker on next_attempt_at",
            "non_retryable": [404, 410, "any other non-408/425/429/5xx"],
        },
        "why": (
            "Sleeping through 26 retries spans about a day. A request thread that did it would hold a "
            "worker, be killed by every proxy in front of the app, and lose the ladder on restart - "
            "which is exactly the in-memory-state defect this repository's audit-first design exists "
            "to prevent. So the policy is split: one attempt inline, the researched delay computed "
            "and stored as next_attempt_at, and the next attempt performed when a driver asks. The "
            "ladder is enforced, not described."
        ),
        "change_it": (
            "RETRY_LADDER / MAX_RETRIES in dsr/event_stream/vocabulary.py, and attempt_delivery in "
            "dsr/event_stream/delivery.py."
        ),
        "blast_radius": "How many attempts a broken endpoint gets, and over how long.",
    },
    {
        "id": "retryable-status-set",
        "topic": "which failures are worth retrying",
        "basis": 'The research documents "429 on rate limit" on the REST seam and nothing about webhook responses.',
        "value": {"retryable": [408, 425, 429, 500, 502, 503, 504], "permanent": "everything else"},
        "why": (
            "429 is sourced. 408/425 and the 5xx family are the ordinary try-again set. Everything "
            "else - notably 404 and 410 - will answer identically twenty-six times over a day, which "
            "makes an operator's logs unreadable and hides the one row that needs a person."
        ),
        "change_it": "RETRYABLE_STATUS in dsr/event_stream/delivery.py.",
        "blast_radius": "Which failures consume the ladder.",
    },
    {
        "id": "key-rotation-overlap",
        "topic": "how long the previous signing secret is offered",
        "basis": 'The research names "signing-secret rotation with `x-seismic-signature-old`" and nothing about when the old secret stops being sent.',
        "value": {
            "generations": 1,
            "headers": {"current": "X-DSR-Signature", "previous": "X-DSR-Signature-Old"},
            "ends_when": [
                "the first delivery that succeeds under the new secret",
                "the next rotation",
            ],
            "confirmation_stored_as": "previous_secret_confirmed, not by clearing previous_secret",
        },
        "why": (
            "The overlap exists so a subscriber that has not yet deployed the new key can still "
            "verify. Closing it on a timer nobody agreed to turns a rotation into an outage, so it "
            "closes on evidence - a success - or is superseded by a newer rotation. The fact that a "
            "rotation happened survives the close, because a reader of the record should be able to "
            "see there was an overlap and when it ended."
        ),
        "change_it": "rotation_overlap and confirm_rotation in dsr/event_stream/signing.py.",
        "blast_radius": "Which deliveries carry a second signature header.",
    },
    {
        "id": "hmac-algorithm-and-header-names",
        "topic": "which hash, and which header names",
        "basis": 'The research says "a HMAC signature" and quotes Seismic\'s `x-seismic-signature`. It names no algorithm and no header of ours.',
        "value": {
            "algorithm": "HMAC-SHA256",
            "format": "sha256=<hex digest over the exact bytes sent>",
            "headers": ["X-DSR-Signature", "X-DSR-Signature-Old", "X-DSR-Event", "X-DSR-Delivery"],
        },
        "why": (
            "Signing Seismic's payload with their header name would be a lie in their logs. SHA-256 "
            "is the ordinary choice and is not researched. The signature is over the exact bytes "
            "sent, because a signature only means anything if what is verified is byte-identical to "
            "what was posted, and json.dumps is not required to be stable across two calls."
        ),
        "change_it": "sign and the *_HEADER constants in dsr/event_stream/signing.py.",
        "blast_radius": "Every subscriber's verification code.",
    },
    {
        "id": "secret-is-stored-in-the-record",
        "topic": "where the signing secret is kept, and what that costs",
        "basis": (
            'The research names a "**secret** (View Key)" that the operator copies and can view again. '
            "It says nothing about storage."
        ),
        "value": {
            "stored_in": "records.data on the webhook row, alongside every other field",
            "consequence": "the audit row for the write contains the secret in after_state",
            "mitigations": [
                "read paths only ever return a masked hint",
                "the raw secret is returned exactly once, at creation and at each rotation",
            ],
        },
        "why": (
            "The honest problem, stated rather than hidden. records.data is the only payload this "
            "store has, and AuditedDatabase writes after_state for every change, so a signing key "
            "stored there is also in the audit log and its JSONL mirror. A separate collection would "
            "have the same property. Storing only a hash is the alternative and it is wrong: "
            "delivery needs the raw key, and a View Key that cannot be re-viewed is not a View Key. "
            "The real fix is encryption at rest or a secrets manager, which is platform work on "
            "AuditedDatabase and therefore not this feature's to do. Masking the read paths is the "
            "part that is this build's, and it means a signing key cannot be read out of a list "
            "response by accident."
        ),
        "change_it": (
            "EndpointBook.build and summarise_webhook in dsr/event_stream/registry.py; the masking "
            "itself in dsr/event_stream/signing.py."
        ),
        "blast_radius": "Anyone with read access to the audit log can read an unrotated signing key.",
    },
    {
        "id": "filters-are-a-subset-and-refuse-to-parse",
        "topic": "the subscription filter language",
        "basis": (
            "The research gives one JSONPath example for the *other* vendor - "
            "`$.data..[?(@.teamSiteId == '1')]` - and says for Dock that 'sub-filtering happens in "
            "the subscriber'."
        ),
        "value": {
            "supported": [
                "$",
                ".name",
                "['name']",
                "..name",
                "..",
                "[n]",
                "[?(@.k op v)]",
                "['a','b']",
                "|",
            ],
            "operators": ["==", "!=", ">", "<", ">=", "<="],
            "unparseable": "refused at compile time, at subscribe time",
            "no_filter": "receives everything it subscribed to (the researched Dock behaviour)",
            "root_alias": "also evaluated against {'data': payload}",
        },
        "why": (
            "A filter that failed to parse and silently matched nothing would be indistinguishable "
            "from 'no activity happened', and the operator would go debug their warehouse instead of "
            "their subscription. So parsing happens at subscribe time and a bad expression is a 400. "
            "The root alias exists so Seismic's own documented example works verbatim against a "
            "payload whose fields are at the top level; without it, copying the researched example "
            "would be the one thing guaranteed to match nothing."
        ),
        "change_it": "dsr/event_stream/filters.py, and compile_filter's call sites in registry.py.",
        "blast_radius": "Which events reach a filtered subscription.",
    },
    {
        "id": "unknown-property-is-refused",
        "topic": "what a misspelled properties parameter does",
        "basis": (
            'The research says "a `properties` query parameter that controls which fields are '
            'included" and that omitting it yields only id, object and url. It says nothing about '
            "unknown names."
        ),
        "value": {
            "unknown_name": "refused with 400, listing the names the collection exposes",
            "omitted": "id, object and url only - never everything",
            "available_names_come_from": "RecordStore.fields, the dynamic index of paths in use",
        },
        "why": (
            "Silently returning nothing is the dangerous default: a client's backfill imports zero "
            "fields and the only symptom is an empty warehouse three days later. This product can "
            "know whether a field exists, so it says so instead of narrowing to nothing. The omission "
            "rule is the same idea from the other side - asking for nothing must mean the minimum, "
            "not everything."
        ),
        "change_it": "require_properties in dsr/event_stream/backfill.py.",
        "blast_radius": "How a backfill client discovers it has a typo.",
    },
    {
        "id": "backfill-rate-limit",
        "topic": "the pull allowance and where it is counted",
        "basis": 'The research documents "429 on rate limit" on the backfill seam, and no number.',
        "value": {
            "limit": 60,
            "window_seconds": 60.0,
            "scope": "per caller, in process, fixed window",
            "header": "Retry-After",
            "shared_counter": False,
        },
        "why": (
            "A low per-minute budget is the ordinary shape for a pull API of this size and it is low "
            "on purpose: a caller paging a large estate is the one most likely to need the limit to "
            "be real. In-process because the research's seam is the documented one and this build "
            "has no shared cache - which is the one thing a multi-process deployment must change, and "
            "it is named here so it is not discovered in production."
        ),
        "change_it": "RateLimiter in dsr/event_stream/backfill.py, constructed per EventStream.",
        "blast_radius": "How fast a backfill client may go.",
    },
    {
        "id": "pull-resource-mapping",
        "topic": "which collections the researched pull paths read",
        "basis": (
            "The research publishes five route paths and no schemas. There is no source field list "
            "to map onto."
        ),
        "value": {
            "workspaces": "room",
            "workspaces/{id}": "room",
            "assets": "document",
            "forms/{id}/responses": "form_response",
            "workspace-plan-tasks": "plan_task",
        },
        "why": (
            "The mapping is the entire content of this inference: the routes are sourced, the "
            "collections are this product's, and the join between them is a judgement. It is served "
            "in /vocabulary so a client can see the mapping instead of guessing which collection a "
            "researched path reads."
        ),
        "change_it": "PULL_RESOURCES in dsr/event_stream/vocabulary.py.",
        "blast_radius": "What every backfill route returns.",
    },
    {
        "id": "event-requires-its-associated-objects",
        "topic": "whether an event can be recorded without the objects its type names",
        "basis": "The research says the payload carries them; it does not say the emitter enforces it.",
        "value": {
            "enforced": True,
            "always": ["workspace", "account"],
            "per_type": "see subscription-type-payloads-inferred",
        },
        "why": (
            "A subscriber written against the researched payload will read "
            "`associatedObjects.workspacePage` for a `workspace.page.viewed` because the research "
            "says it is there. Recording an event without it would make that subscriber's code "
            "throw on a payload this product produced. Refusing at record time is the only place the "
            "mistake can still be fixed."
        ),
        "change_it": "required_objects_for and normalise_associated_objects in vocabulary.py / payloads.py.",
        "blast_radius": "Which events can be recorded at all.",
    },
    {
        "id": "file-upload-response-must-carry-expiry",
        "topic": "what a file_upload form response must contain",
        "basis": (
            'The research says form payloads carry "typed questions incl. `file_upload`" and that '
            'presigned URLs in payloads expire: "The URL expires at `expiresAt` (one hour)".'
        ),
        "value": {
            "required": ["url", "expiresAt"],
            "ttl_seconds": 3600,
            "unknown_question_types": "accepted as opaque strings; only file_upload is special-cased",
            "stored": "presigned_expired and a one-line note, computed at record time and on read",
        },
        "why": (
            "An upload with no expiry is one a subscriber cannot safely keep, and an expired one is a "
            "403 from object storage with no explanation - the classic two-hour bug. Recording whether "
            "it is expired at the moment the event was written is the only place the fact is still "
            "cheap to have. Only `file_upload` is named by the research, so no closed set of question "
            "types is imposed; inventing one would be inventing a requirement."
        ),
        "change_it": "_presigned_response in dsr/event_stream/payloads.py and PRESIGNED_TTL_SECONDS in targets.py.",
        "blast_radius": "Whether a form submission can be recorded, and what a subscriber is told.",
    },
    {
        "id": "room-scope-is-a-filter-not-an-only",
        "topic": "what a room-scoped subscription receives",
        "basis": "The research does not document scoping at all. This product adds it.",
        "value": {
            "room_id_null": "receives every room's events",
            "room_id_set": "receives only that room's events",
        },
        "why": (
            "A subscription with no room is the general-purpose seam the research describes - the one "
            "pushing a whole estate into a warehouse - so it has to receive everything or the "
            "documented behaviour does not exist. Scoping is an addition, not a replacement."
        ),
        "change_it": "SubscriptionBook.list and matching in dsr/event_stream/registry.py.",
        "blast_radius": "Which subscribers a room's events reach.",
    },
    {
        "id": "webhooks-are-account-level",
        "topic": "whether a webhook belongs to a room",
        "basis": 'The research puts webhooks under "**Settings** and click on **Webhooks** from the **Data Management** section", which is account-level.',
        "value": {
            "webhook_room_id": None,
            "subscription_room_id": "optional",
            "event_room_id": "required in practice, not in schema",
        },
        "why": (
            "One URL, one signing secret, one verification handshake: that is an account-level "
            "object, and putting a room on it would mean re-verifying the same URL once per deal. "
            "Scoping belongs on the subscription, which is where the research puts it - the "
            "Subscriptions page under the same account-level Webhook page."
        ),
        "change_it": "The collections and the seed in dsr/event_stream/registry.py.",
        "blast_radius": "Which rows carry a room_id.",
    },
    {
        "id": "backfill-is-a-pull-surface-not-an-export",
        "topic": "what " + "/backfill" + " does and does not do",
        "basis": "The research describes the REST API as the way to backfill missed activity, with no cursor, no since, and no export format.",
        "value": {
            "implemented": ["the five researched paths", "properties", "the 429 limit"],
            "not_implemented": [
                "incremental since/modifiedAt cursors",
                "text/csv output",
                "authentication",
            ],
        },
        "why": (
            "Each of those is documented in the research corpus for a *different* workflow in the same "
            "domain (Seismic's modifiedAt ETL sweep and its text/csv negotiation, for one), not for "
            "this one. Building them here would be building a neighbour's feature. Authentication is "
            "absent for a more basic reason: this product has no auth layer, and inventing one inside "
            "a feature would be worse than saying so."
        ),
        "change_it": "dsr/event_stream/backfill.py and the backfill routes in the feature module.",
        "blast_radius": "What a client can pull and how it is authenticated.",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return dict(entry)
    return None


def describe() -> dict[str, Any]:
    """The whole registry, alongside the half of the workflow that is sourced.

    Both halves in one payload on purpose. The point of this endpoint is that a
    reader can see where the line falls, and that means showing the researched
    vocabulary next to the inferred behaviour rather than only the latter.
    """
    return {
        "count": len(INFERENCES),
        "sourced_quotes": {
            "delivery": SOURCED_QUOTE,
            "subscription_types": SUBSCRIPTION_TYPES_QUOTE,
        },
        "sourced": {
            "event_types": list(EVENT_TYPES),
            "seismic_events": list(SEISMIC_EVENTS),
            "pull_resources": {name: dict(spec) for name, spec in PULL_RESOURCES.items()},
            "timeout_seconds": WEBHOOK_TIMEOUT_SECONDS,
            "retry_ladder_seconds": list(RETRY_LADDER),
            "max_retries": MAX_RETRIES,
        },
        "inferences": [dict(entry) for entry in INFERENCES],
    }
