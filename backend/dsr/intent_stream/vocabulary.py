"""The researched vocabulary of WF-032, served as data rather than compiled in.

Everything here is a decision a caller should be able to read *before* they act,
and the HTTP surface serves it from here so the picker on the page and the
validator in :mod:`dsr.intent_stream.segments` are reading the same list. A
picker built from its own copy of a vocabulary is a picker that drifts.

What is quoted and what is chosen
---------------------------------
Quoted from ``docs/research/digital-sales-room-workflows/wf/WF-032.md``:

* the workflow type is **Webhooks**, chosen from Workflows -> New Workflow;
* a name and "the URL you want to send data to";
* conditions "based on saved Segments from your account";
* "only send a lead once" or "send updates as well";
* payload output "only Company ... or Company + Contacts", with contacts
  "filter[ed] ... on 'Keywords' and required fields";
* a token that is "automatically generated" and "optional to specify";
* the destination may be "a public API for a third party tool or a custom
  solution", and the research names the **Microsoft Teams** and **Google Sheets**
  webhook recipes plus the wider **Integrations & Connectors** collection.

Chosen by this build, and named in :mod:`dsr.intent_stream.inferences` so a
reviewer can disagree with one by name: the field names inside the payload
envelope, the Segment rule operators, the header the token travels in, the
delivery states, the timeout, and the payload's own type string.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Prefixed, unlike most collections in this repository. A hundred features are
# landing at once and the store is one flat namespace, so an unprefixed name
# like "contact" is a coin flip against whoever writes it next. The prefix costs
# nothing and makes the collision impossible by construction.

SEGMENT_COLLECTION = "wf032_segment"
LEAD_COLLECTION = "wf032_lead"
CONTACT_COLLECTION = "wf032_contact"
VISIT_COLLECTION = "wf032_visit"
WORKFLOW_COLLECTION = "wf032_workflow"
DELIVERY_COLLECTION = "wf032_delivery"
STATE_COLLECTION = "wf032_lead_state"

ALL_COLLECTIONS = (
    SEGMENT_COLLECTION,
    LEAD_COLLECTION,
    CONTACT_COLLECTION,
    VISIT_COLLECTION,
    WORKFLOW_COLLECTION,
    DELIVERY_COLLECTION,
    STATE_COLLECTION,
)


# --------------------------------------------------------------------------- #
# The workflow type
# --------------------------------------------------------------------------- #

#: [sourced] "click Workflows and select New Workflow. Choose Webhooks in the
#: popup." One researched workflow type, and the only one this feature builds.
WORKFLOW_TYPE = "webhooks"

#: The popup's other entries are **not** in the sources read. The research names
#: Webhooks and then, separately, a set of *integration surfaces* (Zapier, n8n,
#: Slack, LinkedIn, Sheets, CRM syncs) from the Integrations & Connectors
#: collection. Publishing those as workflow types would be this build inventing
#: a taxonomy the vendor help centre does not state, so they are published as
#: what they are: destinations.
UNRESEARCHED_WORKFLOW_TYPES: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Step 4: the once-or-updates choice
# --------------------------------------------------------------------------- #

#: [sourced] "the option to choose if the workflow should only send a lead once
#: or if it should send updates as well".
SEND_ONCE = "once"
SEND_UPDATES = "updates"
SEND_MODES = (SEND_ONCE, SEND_UPDATES)

#: [sourced] the research lists "only send a lead once" first. The default is that
#: one, which is also the conservative one: a lead the destination has already
#: seen is not worth a second POST unless somebody asked for it.
DEFAULT_SEND_MODE = SEND_ONCE


# --------------------------------------------------------------------------- #
# Step 5: the payload output
# --------------------------------------------------------------------------- #

#: [sourced] "only Company for the company lead".
PAYLOAD_COMPANY = "company"
#: [sourced] "or Company + Contacts for the company lead and contacts employed
#: at the company".
PAYLOAD_COMPANY_CONTACTS = "company_contacts"
PAYLOAD_MODES = (PAYLOAD_COMPANY, PAYLOAD_COMPANY_CONTACTS)

DEFAULT_PAYLOAD_MODE = PAYLOAD_COMPANY

#: The payload's own type string. Chosen, not sourced - the research says only
#: "builds a JSON payload". A stable discriminator is what lets a destination
#: with two workflows pointed at one URL tell the payloads apart.
PAYLOAD_TYPE = "company.intent.matched"


# --------------------------------------------------------------------------- #
# Step 4: conditions
# --------------------------------------------------------------------------- #

#: How several Segments in one workflow's conditions combine. The research says
#: "the conditions you want to be applied to the workflow" without saying
#: whether listing three Segments means three ORs or three ANDs. See
#: ``segment-match-defaults-to-any`` in :mod:`dsr.intent_stream.inferences`.
MATCH_ANY = "any"
MATCH_ALL = "all"
MATCH_MODES = (MATCH_ANY, MATCH_ALL)

DEFAULT_SEGMENT_MATCH = MATCH_ANY

#: Same question one level down, inside one Segment's rules. A Segment of
#: "software OR >1000 employees" is an OR segment; a Segment of
#: "software AND >1000 employees" is an AND segment. Each Segment carries its own
#: ``match``, so one workflow can mix an OR Segment with an AND Segment.
DEFAULT_RULE_MATCH = MATCH_ANY


# --------------------------------------------------------------------------- #
# Step 6: the token
# --------------------------------------------------------------------------- #

#: [sourced] "you have a token to use in your service or tool to prove that
#: traffic is coming from the Albacross platform. It is optional to specify this
#: automatically generated token in your system to verify the Webhook."
#:
#: Where it travels is not stated. A header keeps the JSON body exactly the
#: researched payload, which is what the Teams and Sheets recipes read; a body
#: field is what a spreadsheet recipe with no header access can read. This
#: product sends both, and ``tokenHeader`` in the vocabulary says so. See
#: ``token-travels-in-a-header-and-in-the-body``.
TOKEN_HEADER = "X-Albacross-Token"
TOKEN_BODY_FIELD = "token"
TOKEN_PREFIX = "albwh_"
TOKEN_BYTES = 20

#: The comparison the destination is expected to make. Served as a string rather
#: than implemented here, because the research puts the verification on the
#: receiving side: "use [the token] in **your** service or tool". A test asserts
#: this snippet parses and names the header, so it cannot drift from
#: :data:`TOKEN_HEADER` unnoticed.
TOKEN_VERIFICATION_RECIPE = '''\
import hmac, os

EXPECTED = os.environ["ALBACROSS_WEBHOOK_TOKEN"]

def verify(headers, body) -> bool:
    """True when this POST came from Albacross for this workflow."""
    presented = headers.get("X-Albacross-Token") or body.get("token") or ""
    return hmac.compare_digest(presented, EXPECTED)
'''


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #

#: A delivery is a *decision*, not only a POST. The states below are the
#: researched outcomes plus the two ways the researched rules say not to send.
#: "it was paused" and "the Segment did not match" are two answers an operator
#: most needs, and neither can be reconstructed from a row that was never
#: written - so both are written.
STATE_DELIVERED = "delivered"
STATE_FAILED = "failed"
STATE_SKIPPED = "skipped"
DELIVERY_STATES = (STATE_DELIVERED, STATE_FAILED, STATE_SKIPPED)

#: Why nothing was sent. Each one is a researched rule refusing, not a bug.
SKIP_ALREADY_SENT = "already_sent"
SKIP_INACTIVE = "workflow_inactive"
SKIP_NOT_MATCHED = "segment_not_matched"
SKIP_REASONS = (SKIP_ALREADY_SENT, SKIP_INACTIVE, SKIP_NOT_MATCHED)

#: No timeout is documented anywhere in this workflow's sources. The 26-retry
#: ladder and the 10-second timeout that appear elsewhere in this repository
#: come from a *different* ticket's vendor and are deliberately not borrowed
#: here - see ``no-retry-ladder-and-an-inferred-timeout``.
DEFAULT_TIMEOUT_SECONDS = 10.0

#: How much of the destination's response body is kept. Enough to read an error
#: message out of, bounded so a destination that answers with a stack trace
#: cannot fill the database.
MAX_RESPONSE_BYTES = 2048

#: 2xx is a success. Everything else is not, including 3xx: a redirect the POST
#: did not follow means the payload did not land where it was addressed.
SUCCESS_STATUSES = tuple(range(200, 300))

#: Which failures a re-attempt could plausibly fix. Advisory only - this research
#: specifies no automatic retry, so nothing consults this to schedule anything.
#: It is recorded on the delivery so an operator reading a failed row can see
#: whether trying again is pointless.
RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


# --------------------------------------------------------------------------- #
# Step 5: the contact filters
# --------------------------------------------------------------------------- #

#: [sourced] "If you choose to include Contacts, you have the option of filtering
#: the contact details on 'Keywords' and required fields."
#:
#: Which contact fields a keyword is matched against is not stated. The
#: published set is what makes the filter legible: an operator can see that
#: "security" searches titles and departments and will not search a phone number.
#: See ``keywords-search-a-published-set-of-contact-fields``.
KEYWORD_FIELDS = ("name", "title", "department", "email", "seniority")

#: Both filters are optional, and the research says so: "you **have the
#: option** of filtering". No keywords and no required fields means every
#: contact at the identified company is sent, which is the researched default
#: output for Company + Contacts.
CONTACT_FILTER_FIELDS = ("keywords", "requiredFields")


# --------------------------------------------------------------------------- #
# Destinations
# --------------------------------------------------------------------------- #

#: [sourced] step 7: "combine with a webhook workflow such as 'Integrating with
#: Microsoft Teams via Webhooks' or 'Integrating with Google Sheets via
#: Webhooks'", and the Integrations & Connectors collection the research cites
#: for the wider surface. ``kind`` separates the two documented **webhook
#: recipes** from the other documented **integration surfaces**, because the
#: difference matters to somebody pointing a workflow at a URL: a recipe is a
#: consumer you paste a URL into, a surface is a vendor the research lists.
DESTINATION_RECIPES: tuple[dict[str, Any], ...] = (
    {
        "id": "microsoft-teams",
        "label": "Microsoft Teams",
        "kind": "webhook_recipe",
        "note": (
            "Named in the researched flow as a webhook workflow to combine with: "
            "'Integrating with Microsoft Teams via Webhooks'."
        ),
    },
    {
        "id": "google-sheets",
        "label": "Google Sheets",
        "kind": "webhook_recipe",
        "note": (
            "Named in the researched flow as a webhook workflow to combine with: "
            "'Integrating with Google Sheets via Webhooks'."
        ),
    },
    {
        "id": "slack-private-channel",
        "label": "Slack (private channel)",
        "kind": "webhook_recipe",
        "note": "Listed among the documented integrations a webhook can feed.",
    },
    {
        "id": "zapier",
        "label": "Zapier",
        "kind": "integration_surface",
        "note": "Listed in the researched 'apis_hit' note; no recipe was read.",
    },
    {
        "id": "n8n",
        "label": "n8n",
        "kind": "integration_surface",
        "note": "Listed in the researched 'apis_hit' note; no recipe was read.",
    },
    {
        "id": "linkedin",
        "label": "LinkedIn",
        "kind": "integration_surface",
        "note": "Listed in the researched 'apis_hit' note; no recipe was read.",
    },
    {
        "id": "hubspot",
        "label": "HubSpot",
        "kind": "crm_sync",
        "note": "Listed among the documented CRM syncs.",
    },
    {
        "id": "salesforce",
        "label": "Salesforce",
        "kind": "crm_sync",
        "note": "Listed among the documented CRM syncs.",
    },
    {
        "id": "attio",
        "label": "Attio",
        "kind": "crm_sync",
        "note": "Listed among the documented CRM syncs.",
    },
    {
        "id": "pipedrive",
        "label": "Pipedrive",
        "kind": "crm_sync",
        "note": "Listed among the documented CRM syncs.",
    },
)


# --------------------------------------------------------------------------- #
# The explainer
# --------------------------------------------------------------------------- #

#: [sourced] The research lists a dedicated "**What are Webhooks?**" explainer
#: among the features and tools in play. This is that explainer, in the product,
#: built from the quoted evidence rather than from a paragraph of our own: every
#: claim below carries the source id it came from.
EVIDENCE: tuple[dict[str, str], ...] = (
    {
        "id": "what-it-is-for",
        "source": "integrating-with-webhooks",
        "quote": (
            "This is a guide for setting up an Albacross Workflow in order to "
            "automatically export your leads via a Webhook to your own system, third "
            "party applications supporting Webhooks, or simply for storing them in a "
            "JSON format."
        ),
    },
    {
        "id": "needs-a-destination",
        "source": "integrating-with-webhooks",
        "quote": (
            "In order to create an Albacross Workflow via Webhooks, you'll need a "
            "destination that can handle receiving the data object being sent via the "
            "webhooks. This can be a public API for a third party tool or a custom "
            "solution."
        ),
    },
    {
        "id": "name-and-url",
        "source": "integrating-with-webhooks",
        "quote": (
            "When logged in to Albacross, click Workflows and select New Workflow. "
            "Choose Webhooks in the popup. Add a name for your Workflow and the URL "
            "you want to send data to."
        ),
    },
    {
        "id": "conditions-and-send-mode",
        "source": "integrating-with-webhooks",
        "quote": (
            "Add the conditions you want to be applied to the workflow to sort out "
            "which leads you want your Workflow to send based on saved Segments from "
            "your account. You will also have the option to choose if the workflow "
            "should only send a lead once or if it should send updates as well. If "
            "you choose to send updates as well, you will receive the same lead with "
            "updated activity data if that lead visits your webpage again."
        ),
    },
    {
        "id": "payload-output",
        "source": "integrating-with-webhooks",
        "quote": (
            "Choose the output of data you want to be sent to your URL, for example "
            "only Company for the company lead or Company + Contacts for the company "
            "lead and contacts employed at the company. If you choose to include "
            "Contacts, you have the option of filtering the contact details on "
            "'Keywords' and required fields."
        ),
    },
    {
        "id": "token",
        "source": "integrating-with-webhooks",
        "quote": (
            "As an option for security measures, you have a token to use in your "
            "service or tool to prove that traffic is coming from the Albacross "
            "platform. It is optional to specify this automatically generated token in "
            "your system to verify the Webhook."
        ),
    },
)

#: The seven steps of the researched user flow, in order, each with the route
#: that carries it. Served so the page can show a reviewer the flow and the
#: endpoint side by side, and so a step with no route is visible as such.
FLOW: tuple[dict[str, Any], ...] = (
    {"step": 1, "text": "Workflows -> New Workflow", "route": "GET /vocabulary"},
    {"step": 2, "text": "Choose Webhooks in the popup", "route": "GET /vocabulary"},
    {
        "step": 3,
        "text": "Name the workflow and enter the destination URL that will accept the POST",
        "route": "POST /workflows",
    },
    {
        "step": 4,
        "text": (
            "Add conditions selecting which leads to send, based on saved Segments; "
            "choose whether to send a lead only once or to send updates as well"
        ),
        "route": "POST /workflows with conditions, sendMode",
    },
    {
        "step": 5,
        "text": (
            "Choose the payload output: Company only, or Company + Contacts, optionally "
            "filtering contact fields on 'Keywords' and required fields"
        ),
        "route": "POST /workflows with payload, contactFilter",
    },
    {
        "step": 6,
        "text": (
            "Save changes. Optionally supply the automatically generated token so the "
            "destination can verify traffic is from Albacross"
        ),
        "route": "PATCH /workflows/{workflow_id}, POST /workflows/{workflow_id}/token",
    },
    {
        "step": 7,
        "text": (
            "Start receiving JSON at the destination; combine with a webhook workflow "
            "such as Microsoft Teams or Google Sheets"
        ),
        "route": "GET /deliveries, GET /destinations",
    },
)


def vocabulary() -> dict[str, Any]:
    """Every list a client needs to render a picker without hard-coding one."""
    return {
        "ticket": "WF-032",
        "workflowType": WORKFLOW_TYPE,
        "workflowTypes": [WORKFLOW_TYPE],
        "unresearchedWorkflowTypes": list(UNRESEARCHED_WORKFLOW_TYPES),
        "sendModes": list(SEND_MODES),
        "defaultSendMode": DEFAULT_SEND_MODE,
        "payloadModes": list(PAYLOAD_MODES),
        "defaultPayloadMode": DEFAULT_PAYLOAD_MODE,
        "segmentMatchModes": list(MATCH_MODES),
        "defaultSegmentMatch": DEFAULT_SEGMENT_MATCH,
        "defaultRuleMatch": DEFAULT_RULE_MATCH,
        "payloadType": PAYLOAD_TYPE,
        "deliveryStates": list(DELIVERY_STATES),
        "skipReasons": list(SKIP_REASONS),
        "keywordFields": list(KEYWORD_FIELDS),
        "contactFilterFields": list(CONTACT_FILTER_FIELDS),
        "token": {
            "header": TOKEN_HEADER,
            "bodyField": TOKEN_BODY_FIELD,
            "prefix": TOKEN_PREFIX,
            "autoGenerated": True,
            "enforcedBy": "the destination system",
            "verificationRecipe": TOKEN_VERIFICATION_RECIPE,
        },
        "timeoutSeconds": DEFAULT_TIMEOUT_SECONDS,
        "maxResponseBytes": MAX_RESPONSE_BYTES,
        "collections": list(ALL_COLLECTIONS),
    }
