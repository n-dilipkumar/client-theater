"""WF-082: every researched term this workflow validates against.

This module is the first of the four that carry WF-082, and it is the only one that
contains no judgement. Every constant below is either a quote from the researched
specification for this workflow or a name this build gives to a thing the
specification describes in prose. The specification is
``docs/research/digital-sales-room-workflows/wf/WF-082.md``, quoted in full in issue
129, and the docstring on each constant names the sentence it came from.

The three checks, and why they are three
---------------------------------------

The specification's user flow says a handler "verifies authenticity three ways:
source-IP allowlist from a published JSON range file, the ``Content-Sha256`` header
(base64 SHA-256 of the JSON payload keyed with the API key), and recomputing the
``event_hash`` HMAC over ``event_time`` + ``event_type``". The data flow then says
"Only then does the handler act."

So there are three checks, they run in that order, and none of them substitutes for
another. Two of them are keyed HMAC-SHA256 values and a reader could reasonably
conflate them, so this module states the difference once and the code below never
has to guess:

* :data:`CONTENT_SHA256_HEADER` covers **the whole JSON payload** and is **base64**.
  Evidence: "``echo -n $json | openssl dgst -sha256 -hmac $apiKey``".
* :data:`EVENT_HASH_FIELD` covers **``event_time`` concatenated with
  ``event_type``, with no separator**, and is the hex digest.
  Evidence: "``echo -n $event_time$event_type | openssl dgst -sha256 -hmac $apikey``".

An implementation that accepted either value for either field would authenticate a
payload it never actually read, and an implementation that concatenated with a
separator would reject every real event. Both are single-character mistakes with a
total outage as the result, so both are pinned by a test rather than left to review.

The words the module refuses to blur
------------------------------------

:data:`ACKNOWLEDGEMENT_BODY` is a **magic string**, not a status code. The evidence is
explicit: "the callback url must return an HTTP ``200`` with a response body that
contains the string ``Hello API Event Received``". The response body is the thing the
provider checks, and this workflow serves it for free on every accepted delivery,
including a duplicate, because a duplicate the provider reads as a failure costs a
retry and a retry walks a 20 hour ladder.

:data:`PROVIDER_TIMEOUT_SECONDS` and :data:`RETRY_LADDER_SECONDS` are the contract
this product owes its consumers, not tuning. The evidence says "our requests will
timeout after **30 seconds**" and gives six retry intervals and a self-disable
threshold. A handler that answers slowly does not merely lose an event: after
:data:`CONSECUTIVE_FAILURE_LIMIT` consecutive failures the provider "will
automatically" clear the callback URL, which is a silent data loss with no error
anywhere. The ladder is therefore served as data at ``GET /retry-policy`` under this
feature's own prefix, rather than described in prose, because the stated reason is
retry logic built "instead of scraping the human-readable page".

The prefix is :data:`ROUTER_PREFIX` below rather than a string repeated in prose,
because a docstring that names a URL the app does not serve is the same defect the
contract forbids in an audit ``source``. The feature router builds every audit source
from that constant, and a test asserts each one matches a mounted route.
"""

from __future__ import annotations

from typing import Any

#: The prefix the WF-082 feature router mounts, and the only prefix this workflow serves.
#:
#: Duplicated from the feature module's ``router.prefix`` so this domain package does not
#: have to import a FastAPI router to know where it is published. The feature module asserts
#: at import time that the two agree, so a change to one cannot leave the other stale.
ROUTER_PREFIX = "/api/wf082"

# --------------------------------------------------------------------------- #
# The callback registration
# --------------------------------------------------------------------------- #

#: The request header the provider sets on every inbound callback. Evidence: the
#: data sources line names "headers ``User-Agent: Dropbox Sign API`` and
#: ``Content-Sha256``".
USER_AGENT = "Dropbox Sign API"

#: The header carrying the keyed digest of the whole payload. Evidence: "A base64
#: encoded SHA256 signature of the request's JSON payload, generated using your API
#: key."
CONTENT_SHA256_HEADER = "Content-Sha256"

#: The payload part name. Evidence: "Provider POSTs events as
#: ``multipart/form-data`` with the payload in a field named ``json``". The request
#: is not a raw JSON body, so ``request.json()`` cannot read it and a route that
#: declared a JSON body would reject every real delivery.
JSON_PART = "json"

#: The two places a callback URL is registered, in the specification's own words:
#: "Integrator registers a callback URL at the account level (``callback_url`` on
#: ``/account``) or per API app (``callback_url`` on ``/api_app/{client_id}``)".
CALLBACK_SCOPES = ("account", "api_app")

#: Evidence: "we will require callback URLs to use **HTTPS** starting **November 30,
#: 2024**... Any callback URL not using HTTPS on December 1, 2024, will stop
#: receiving Sign callback events." The scheme is therefore not a preference and the
#: date is not advisory: a plain-HTTP callback stops receiving events.
REQUIRED_SCHEME = "https"

#: The date the specification gives, kept because the rule has one. It is served in
#: the retry-policy payload so a reader can see when the rule took effect rather than
#: having to trust a summary.
TLS_ENFORCEMENT_DATE = "2024-11-30"

#: Evidence: the second URL the provider uses, the one whose events are scoped to a
#: single API app rather than to the whole account.
APP_PATH_TEMPLATE = "/api_app/{client_id}"


# --------------------------------------------------------------------------- #
# The acknowledgement
# --------------------------------------------------------------------------- #

#: Evidence: "the callback url must return an HTTP ``200`` with a response body that
#: contains the string ``Hello API Event Received``. If no response is received,
#: Dropbox Sign considers that a failed callback and the event will be sent again
#: later."
ACKNOWLEDGEMENT_BODY = "Hello API Event Received"

#: The status the magic string rides on. Evidence names 200.
ACKNOWLEDGEMENT_STATUS = 200


# --------------------------------------------------------------------------- #
# The event payload
# --------------------------------------------------------------------------- #

#: Evidence: "Every event payload contains an ``event_hash`` that can be used to
#: verify the event is coming from Dropbox Sign".
EVENT_HASH_FIELD = "event_hash"

#: Evidence: "``echo -n $event_time$event_type | openssl dgst -sha256 -hmac
#: $apikey``". The concatenation carries **no separator**, which is stated here
#: because it is the one character of this workflow's signing scheme that a reader
#: would otherwise supply out of habit.
EVENT_HASH_SEPARATOR = ""

#: The two fields the event HMAC covers, in the order the shell line names them.
EVENT_HASH_INPUT_FIELDS = ("event_time", "event_type")

#: The event id, and the field the specification dedupes on: "Handler de-duplicates
#: on event id". :data:`EVENT_METADATA_KEYS` does not include it, because the payload
#: shape the evidence quotes puts the id at the top of the event object, not inside
#: the metadata block.
EVENT_ID_FIELD = "event_id"

#: Evidence, from the data flow: the payload parses into
#: ``{event:{event_time, event_type, event_hash, event_metadata{related_signature_id,
#: reported_for_account_id, reported_for_app_id}}, signature_request:{...}}``.
EVENT_METADATA_KEYS = (
    "related_signature_id",
    "reported_for_account_id",
    "reported_for_app_id",
)

#: The two sibling objects the evidence names beside ``event``.
PAYLOAD_SIBLINGS = ("signature_request",)

#: Evidence: "Filters key off ``event_type`` - ``signature_request_all_signed`` vs
#: ``signature_request_downloadable``". The specification attaches a warning to the
#: pair that this workflow carries as data rather than as a comment:
#: "final document generation lags signing, so If you plan to download the final
#: files, wait for ``signature_request_downloadable``."
SIGNATURE_REQUEST_ALL_SIGNED = "signature_request_all_signed"
SIGNATURE_REQUEST_DOWNLOADABLE = "signature_request_downloadable"

#: The filter key is ``event_type`` and nothing else. Evidence: "Filters key off
#: ``event_type``".
EVENT_TYPE_FIELD = "event_type"


# --------------------------------------------------------------------------- #
# The IP range file
# --------------------------------------------------------------------------- #

#: Evidence: the published range file, "automatically updated if the IP addresses
#: change". The specification's sources block writes this host with a stray backtick
#: inside the angle brackets; the bare URL is used here and the typo is not chased.
IP_RANGES_URL = "https://dropbox-sign-api-config.s3.amazonaws.com/ip-ranges.json"

#: Evidence: "We have made a JSON file containing the full list of IP addresses that
#: webhook events may come from available for download... We recommend checking this
#: list periodically to ensure your callback handler is secure."
IP_RANGES_RECHECK = (
    "We recommend checking this list periodically to ensure your callback handler is secure."
)


# --------------------------------------------------------------------------- #
# The three checks, in order
# --------------------------------------------------------------------------- #

#: The check order the data flow fixes. IP first, then the payload digest, then the
#: event digest. It is published rather than merely followed because the order is
#: part of the contract: a handler that checked the expensive HMAC first would spend
#: its 30 second budget on requests that the IP check would have refused for free.
IP_ALLOWLIST = "ip_allowlist"
CONTENT_SHA256 = "content_sha256"
EVENT_HASH = "event_hash"

#: The three checks, in the order they run. A delivery must satisfy all three.
CHECK_ORDER = (IP_ALLOWLIST, CONTENT_SHA256, EVENT_HASH)

#: A delivery that satisfies every check. This is the only state in which the
#: handler acts, because the data flow says "Only then does the handler act."
VERIFIED = "verified"

#: The delivery was refused. One reason per delivery, taken from the first check that
#: failed in :data:`CHECK_ORDER`.
REJECTED = "rejected"

#: The delivery passed every check and its event id has already been recorded. The
#: provider retrying an event it did not see acknowledged is normal, not an error, so
#: this is a state of its own rather than a rejection.
DUPLICATE = "duplicate"

#: Every state one recorded delivery can be in.
DELIVERY_STATES = (VERIFIED, REJECTED, DUPLICATE)


# --------------------------------------------------------------------------- #
# The retry contract
# --------------------------------------------------------------------------- #

#: Evidence: "our requests will timeout after **30 seconds**, so callbacks will fail
#: if your server takes longer than that to respond."
PROVIDER_TIMEOUT_SECONDS = 30

#: Evidence: "we will retry POSTing the event up to **6 times**, with each retry
#: interval being longer than the previous one" - First 5 minutes, Second 15 minutes,
#: Third 45 minutes, Fourth 2 h 15 m, Fifth 6 h 45 m, Sixth 20 h 15 m.
RETRY_LADDER_SECONDS = (300, 900, 2700, 8100, 24300, 72900)

#: Evidence: "After **10 consecutive failures**, your callback URL will be
#: automatically cleared." Clearing the callback is silent data loss: the provider
#: stops sending and the handler stops hearing, with no error on either side. This is
#: why the ladder is owed to consumers as data.
CONSECUTIVE_FAILURE_LIMIT = 10

#: How the ladder is described to a consumer that has to schedule its retries. The
#: specification says each interval is "longer than the previous one" and then gives
#: six numbers, so the multiplier is published rather than assumed: three times the
#: previous interval, and the arithmetic is checked in the tests.
RETRY_MULTIPLIER = 3


def retry_ladder() -> list[dict[str, Any]]:
    """The retry ladder as a consumer reads it: cumulative seconds per attempt.

    The specification gives six intervals and no total, and a consumer retrying the
    last one needs to know how long the whole ladder runs: the sixth retry lands
    32 hours 45 minutes after the first attempt. Served as data so the number is
    computed once, here, rather than restated in a page, a doc and a test.
    """
    rows: list[dict[str, Any]] = []
    cumulative = 0
    for attempt, interval in enumerate(RETRY_LADDER_SECONDS, start=1):
        cumulative += interval
        rows.append(
            {
                "attempt": attempt,
                "interval_seconds": interval,
                "cumulative_seconds": cumulative,
                "cumulative_label": format_delay(cumulative),
            }
        )
    return rows


def format_delay(seconds: int) -> str:
    """A delay in seconds as hours, minutes and seconds, without a Unicode arrow.

    The arrow is banned on purpose. A single U+2192 RIGHTWARDS ARROW in one recovered
    feature's seed return string broke the entire seeder on a Windows console, so
    every string this workflow renders or prints is written with ASCII only, and a
    test encodes this one as cp1252.
    """
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} h")
    if minutes:
        parts.append(f"{minutes} m")
    if secs or not parts:
        parts.append(f"{secs} s")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# The machine-readable error catalogue
# --------------------------------------------------------------------------- #

#: Evidence: "machine-readable error catalogues in the OpenAPI spec: root
#: ``x-error-codes`` (keyed by ``error_name``, with ``http_status``, ``cause``,
#: ``remediation``, ``retryable``, ``backoff``) and ``x-error-events`` for
#: asynchronous webhook errors."

#: The keys each catalogue entry carries, in the order the specification lists them.
ERROR_CODE_FIELDS = ("http_status", "cause", "remediation", "retryable", "backoff")

#: The two catalogues, by the names the specification gives them.
ERROR_CODES_CATALOGUE = "x-error-codes"
ERROR_EVENTS_CATALOGUE = "x-error-events"

#: One entry per refusal this handler can produce, keyed by ``error_name``.
#:
#: Every entry carries all five researched fields. ``retryable`` is not a guess: it
#: follows from the provider's own ladder, where a retry the provider makes is worth
#: something only when the cause would clear. An IP that is not on the list clears
#: when the range file changes, so it is retryable. A payload whose ``Content-Sha256``
#: does not verify does not clear, so it is not. A duplicate clears, because this
#: handler answers duplicates with the magic string rather than refusing them.
ERROR_CODES: dict[str, dict[str, Any]] = {
    "callback_not_registered": {
        "http_status": 400,
        "cause": "No callback registration is bound to the API key that signed this delivery.",
        "remediation": (
            "Register a callback_url for this account or this API app, then retry the event."
        ),
        "retryable": False,
        "backoff": None,
        "retry_after_seconds": None,
    },
    "callback_not_https": {
        "http_status": 400,
        "cause": "The registered callback URL does not use HTTPS.",
        "remediation": "Change callback_url to an HTTPS URL. Plain HTTP callbacks receive no events.",
        "retryable": False,
        "backoff": None,
        "retry_after_seconds": None,
    },
    "payload_part_missing": {
        "http_status": 400,
        "cause": "The request carried no form part named json.",
        "remediation": "Send the payload as multipart/form-data with a part named json.",
        "retryable": False,
        "backoff": None,
        "retry_after_seconds": None,
    },
    "payload_not_json": {
        "http_status": 400,
        "cause": "The json part is not parseable JSON.",
        "remediation": "Send the payload the provider documents. The json part must parse.",
        "retryable": True,
        "backoff": "the retry ladder",
        "retry_after_seconds": None,
    },
    "source_ip_not_allowed": {
        "http_status": 403,
        "cause": "The source IP is not in the published range file.",
        "remediation": (
            "Refresh the allowlist from the published range file, then retry. The file "
            "is updated automatically when the provider's addresses change."
        ),
        "retryable": True,
        "backoff": "the retry ladder",
        "retry_after_seconds": None,
    },
    "no_source_ip": {
        "http_status": 403,
        "cause": "The request carried no source address to check against the allowlist.",
        "remediation": (
            "This handler reads the socket peer address. A deployment behind a proxy that "
            "removes the peer address has turned the IP check off, and a callback URL that "
            "receives no events is the symptom."
        ),
        "retryable": False,
        "backoff": None,
        "retry_after_seconds": None,
    },
    "content_sha256_missing": {
        "http_status": 401,
        "cause": "The Content-Sha256 header was absent.",
        "remediation": "Send the Content-Sha256 header on every callback.",
        "retryable": False,
        "backoff": None,
        "retry_after_seconds": None,
    },
    "content_sha256_malformed": {
        "http_status": 401,
        "cause": "The Content-Sha256 header was not base64.",
        "remediation": (
            "The header is a base64 digest of the JSON payload, as "
            "'echo -n $json | openssl dgst -sha256 -hmac $apiKey' produces. Send base64, "
            "not hex: the event_hash check is the one that uses hex."
        ),
        "retryable": False,
        "backoff": None,
        "retry_after_seconds": None,
    },
    "content_sha256_mismatch": {
        "http_status": 401,
        "cause": "Content-Sha256 does not match the digest of the payload this handler received.",
        "remediation": (
            "Check that the payload was forwarded byte for byte. A proxy that re-encodes "
            "the body changes the digest."
        ),
        "retryable": False,
        "backoff": None,
        "retry_after_seconds": None,
    },
    "event_hash_mismatch": {
        "http_status": 401,
        "cause": "event_hash does not match HMAC-SHA256(api_key, event_time + event_type).",
        "remediation": (
            "Check the API key and the event_time and event_type values. The HMAC input "
            "is the two values concatenated with no separator."
        ),
        "retryable": False,
        "backoff": None,
        "retry_after_seconds": None,
    },
    "event_hash_missing": {
        "http_status": 400,
        "cause": "The payload carried no event_hash.",
        "remediation": "Every provider event carries an event_hash. Send the provider's payload.",
        "retryable": False,
        "backoff": None,
        "retry_after_seconds": None,
    },
    "event_type_missing": {
        "http_status": 400,
        "cause": "The payload carried no event_type, so the filter key is absent.",
        "remediation": "Every provider event carries an event_type. Send the provider's payload.",
        "retryable": False,
        "backoff": None,
        "retry_after_seconds": None,
    },
    "event_time_missing": {
        "http_status": 400,
        "cause": "The payload carried no event_time, so the HMAC input is not the two values.",
        "remediation": (
            "Every provider event carries an event_time. The event_hash covers event_time "
            "concatenated with event_type, so a payload missing one of the two cannot be "
            "verified at all."
        ),
        "retryable": False,
        "backoff": None,
        "retry_after_seconds": None,
    },
    "event_id_missing": {
        "http_status": 400,
        "cause": ("The payload carried no event_id, so the delivery cannot be de-duplicated."),
        "remediation": (
            "Every provider event carries an event_id. Send the provider's payload. A "
            "delivery without one cannot be de-duplicated, so this handler refuses it "
            "rather than accepting an event it cannot recognise again."
        ),
        "retryable": False,
        "backoff": None,
        "retry_after_seconds": None,
    },
    "api_key_missing": {
        "http_status": 500,
        "cause": "The registration names no API key, so no delivery can be authenticated.",
        "remediation": (
            "This is a configuration fault on this side. Store the account's API key on the "
            "callback registration."
        ),
        "retryable": False,
        "backoff": None,
        "retry_after_seconds": None,
    },
}


def error_code(name: str) -> dict[str, Any]:
    """One catalogue entry, or the ``unknown_error_name`` fallback.

    The fallback keeps a catalogue reader total: a consumer asking for a name this
    build does not have gets a well formed entry saying so, rather than a KeyError
    that looks like a crash in the provider's integration.
    """
    entry = ERROR_CODES.get(name)
    if entry is not None:
        return {"error_name": name, **entry}
    return {
        "error_name": name,
        "http_status": 400,
        "cause": f"This build has no catalogue entry named {name}.",
        "remediation": "Read the catalogue served by this API and use a name it contains.",
        "retryable": False,
        "backoff": None,
        "retry_after_seconds": None,
    }


# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: Every collection this workflow writes. The names are JSON in ``records.data``, so
#: a team adding a field needs no migration and no coordination with anyone.
COLLECTION_CALLBACKS = "inbound_callback"
COLLECTION_RANGES = "inbound_ip_range"
COLLECTION_DELIVERIES = "inbound_webhook_delivery"
COLLECTION_DEDUPE = "inbound_webhook_dedupe"

ALL_COLLECTIONS = (
    COLLECTION_CALLBACKS,
    COLLECTION_RANGES,
    COLLECTION_DELIVERIES,
    COLLECTION_DEDUPE,
)

#: The payload-side room reference. Not ``room_id``: that key is part of the record
#: envelope and the store strips it out of ``data`` before the dynamic index is
#: built, so a row that stored its room there would be unfilterable.
ROOM_REF = "room_ref"

#: The data key the sealed API key is stored under. The key is a shared secret and a
#: registration is readable through the core records API, so the secret is sealed at
#: rest rather than kept in the clear.
SEALED_API_KEY = "sealed_api_key"

#: The public fingerprint of the key that sealed a row, so a row can say which key
#: sealed it without revealing the key.
KEY_FINGERPRINT = "key_fingerprint"

#: Where the sealing key came from: ``env`` when the operator set one, ``default``
#: when this process is running on the published demo key. Named ``VAULT`` rather
#: than ``API_KEYS`` because it holds the key that seals the API keys, not the API
#: keys themselves, and a variable whose name is one thing and whose value is
#: another is how a deployment ends up storing provider secrets in it.
KEY_ORIGIN = "key_origin"
KEY_ENV = "DSR_WEBHOOK_VAULT_KEY"

#: **Not a secret.** The published fallback so a fresh checkout's demo works. Every
#: surface that reads a registration says which key sealed it, and the demo key says
#: so in the answer it returns.
DEMO_API_KEY = "dsr-demo-inbound-api-key"

#: The header an integrator presents to pick a registration when more than one is
#: bound to the room. Evidence gives no such header, so it is an inference and it is
#: named as one.
CREDENTIAL_HEADER = "X-DSR-Inbound-Key"
