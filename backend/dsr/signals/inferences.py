"""Every judgement call in this package, in one inspectable place.

The research for WF-027 is unusually specific about most of this workflow: it
enumerates the fields of a signal registration and of an emitted signal, it names
the three urgency values, it says the idempotency rule, and it gives a worked
example of an indicator that is specific next to one that is not. What it does
*not* do is say how a sender behaves on the edges of that description, and the
edges are where a build has to decide something.

Those decisions are collected here rather than left as comments in function
bodies, because a judgement call in a comment is one nobody re-reads and a wrong
one becomes product behaviour without anyone noticing. Each entry is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``GET /api/wf-027/inferences``, so a
  reviewer reads the list instead of reconstructing it from a diff.

Nothing here is a migration, a typed column, or a new required field. It is a list
of ordinary JSON, exactly like everything else this product stores, and it is a
*record* of a judgement rather than a mechanism that enforces one.

One entry is not an inference but a boundary, and is listed for that reason:
``json-schema-subset`` records what :mod:`dsr.signals.schema` does not
implement, because a partner authoring a ``data_shape`` needs to know where the
edge is before they hit it.
"""

from __future__ import annotations

from typing import Any

from dsr.signals import icume, indicators, schema
from dsr.signals.vocabulary import (
    ACTIONABILITY_NOTE,
    ATTRIBUTION_PRECEDENCE,
    ATTRIBUTION_TYPES,
    DEFAULT_URGENCY,
    URGENCIES,
    resolve_locale,
)

#: The sentence from the research that governs most of what follows: the fields
#: are named, the behaviour on their edges is not.
SOURCED_QUOTE = (
    "On each qualifying DSR interaction, emit a live signal: POST "
    "https://api.salesloft.com/v2/signals.json with type, data, indicators[], urgency "
    "(high/medium/low), occurred_at, idempotency_key (UUID4), attribution object, and "
    "broadcast_notification."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "attribution-precedence",
        "topic": "which attribution value decides the receiving seller when a signal carries several",
        "topic_note": "the largest inference in the package",
        "basis": (
            'The research says "Salesloft will use the attribution value to derive the '
            'appropriate Salesloft user to receive the signal" and names five values, but '
            "publishes no order across them. Section 13 of the same research file, which is a "
            "different workflow and is NOT implemented here, states the assignment precedence "
            'as "User, Content, Person, Account". That is the only sourced order this product '
            "has, so it is followed rather than replaced with an invented one."
        ),
        "value": {
            "order": list(ATTRIBUTION_PRECEDENCE),
            "opportunity_id_rationale": (
                "opportunity_id has no place in the sourced sentence. It is slotted directly "
                "after user_guid because an opportunity names one deal and one owner, which is "
                "more specific than Email Content, a Person, or an Account."
            ),
        },
        "why": (
            "The receiving seller is what the Live Feed is for, so the derivation cannot be left "
            "undefined. Resolving_receiver reports the value that decided it, so a reader can "
            "check the reasoning rather than trust it."
        ),
        "change_it": (
            "ATTRIBUTION_PRECEDENCE in dsr/signals/vocabulary.py. The resolver, the feed and the "
            "vocabulary endpoint all read that one tuple."
        ),
        "blast_radius": (
            "Which attribution value each signal names as the one that reached a seller, and the "
            "order of the `considered` list on every signal."
        ),
    },
    {
        "id": "receiver-is-the-room-owner",
        "topic": "where the receiving seller's identity comes from",
        "basis": (
            "The research says the receiving user is derived from the attribution and that "
            "Salesloft hydrates the signal to a Person/Account. It says nothing about where the "
            "owning user comes from, and this product's rooms carry an `owner`."
        ),
        "value": {
            "seller": "the owner of the room the interaction happened in",
            "unresolved_when": "the room has no owner",
            "never_invented": True,
        },
        "why": (
            "A Live Feed entry addressed to nobody is a bug that should be visible. Reporting "
            "resolved: false is honest; picking a default owner would put a buyer's intent in "
            "front of the wrong rep."
        ),
        "change_it": "resolve_receiver in dsr/signals/emission.py.",
        "blast_radius": "The seller filter on the Live Feed, and the by_seller counts.",
    },
    {
        "id": "default-urgency",
        "topic": "the urgency a signal gets when the sender omits it",
        "basis": (
            "The research names urgency and fixes its three accepted values but says nothing "
            "about a default."
        ),
        "value": {"default": DEFAULT_URGENCY, "accepted": list(URGENCIES)},
        "why": (
            "Medium is the middle of the three values the research names, so an unclassified "
            "signal lands in the middle of the feed rather than at either extreme. Emitting with "
            "a warning rather than refusing, because a signal whose only flaw is an unclassified "
            "urgency is still the buyer's intent and is worth a seller's attention."
        ),
        "change_it": "DEFAULT_URGENCY in dsr/signals/vocabulary.py.",
        "blast_radius": "Signals emitted without urgency, and their position in the feed.",
    },
    {
        "id": "default-broadcast",
        "topic": "whether a signal reaches the Live Feed when neither it nor its registration says",
        "basis": (
            'The research says broadcast_notification "controls Live Feed display" on both the '
            "registration and the signal, and states no default."
        ),
        "value": {"default": True},
        "why": (
            "The flow the research describes ends with the signal being published to the Live "
            "Feed, so a registered signal type is presumed to be one a seller should see. A "
            "registration that is not gets broadcast_notification set to false, and the seeded "
            "demo carries one so the other branch is exercised."
        ),
        "change_it": "The default in normalise_registration in dsr/signals/registration.py.",
        "blast_radius": "Which signals appear in the Live Feed, and the withheld count.",
    },
    {
        "id": "locale-fallback-chain",
        "topic": "which locale a description renders in when the requested one is absent",
        "basis": ("The research requires a localized description and gives no fallback rule."),
        "value": {
            "chain": ["the exact tag", "the bare language", "en", "the first locale declared"],
            "always_reported": "locale_resolved and locale_fallback travel on every render",
        },
        "why": (
            "A seller reading a German sentence because that is the only German registration is "
            "fine. A seller reading English without being told why is not, so the fallback is "
            "reported rather than performed quietly."
        ),
        "change_it": "resolve_locale in dsr/signals/vocabulary.py.",
        "blast_radius": "The rendered sentence on every signal, and the warning beside it.",
    },
    {
        "id": "indicator-bound-grammar",
        "topic": "how an indicator's bound is read out of its key",
        "basis": (
            "The research contrasts spent_more_than_30s_on_site with time_spent_on_site and "
            'concludes "Indicators should be very specific", but publishes no grammar for '
            "encoding a bound."
        ),
        "value": {
            "form": "<subject> <comparative> <number><unit?> <rest...>",
            "comparatives": sorted(indicators.COMPARATIVES),
            "observation_resolved_from": "metadata_shape, and only when it names exactly one numeric field",
            "ambiguous_shape": "qualifies on trust, flagged bound_unresolvable",
            "unrecognised_key": "qualifies on trust, flagged no_bound_claimed",
        },
        "why": (
            "The research's own good indicator puts a bound in the key and its evidence in the "
            "metadata, so a key that states a bound can be held to it. A key that does not is "
            "not a failure - the research offers specificity as advice - so it qualifies on "
            "trust and the signal says so, which keeps the trust visible instead of silent. The "
            "same goes for a metadata_shape that names two numeric fields: the research supplies "
            "no rule for choosing between them, and the one example it does give "
            "(spent_more_than_30s_on_site against time_in_seconds) shares no word with the field "
            "name, so a name-similarity heuristic would not have worked on it. An unambiguous "
            "shape is checked; an ambiguous one is reported."
        ),
        "change_it": "COMPARATIVES and parse_claim in dsr/signals/indicators.py.",
        "blast_radius": (
            "Every emission: which indicators qualify, and which qualify without being checked."
        ),
    },
    {
        "id": "emission-requires-an-indicator",
        "topic": "whether a signal may carry no indicators at all",
        "basis": (
            'The data flow matches every interaction "to a registered indicator with quantified '
            'metadata", and the research says "A signal should have high value and should drive '
            'a seller to act". Neither sentence forbids an empty array outright.'
        ),
        "value": {"rule": "a signal with no indicators is refused", "status": 400},
        "why": (
            "An indicator is the only part of a signal a seller reads. A signal that matched "
            "nothing has nothing to show, and storing it would put an unreadable row in the "
            "product's history."
        ),
        "change_it": "check_indicators in dsr/signals/emission.py.",
        "blast_radius": "The emit route. The interaction route reports rather than raising.",
    },
    {
        "id": "bound-not-met-is-a-refusal-not-a-drop",
        "topic": "what happens when a signal's own evidence contradicts its indicator",
        "basis": (
            'The research does not discuss it. "Indicators should be very specific" is the '
            "closest line, and the indicator is what is rendered to a seller."
        ),
        "value": {
            "emit_route": "refused with 400 indicator_bound_not_met",
            "interaction_route": "reported as a decision, nothing stored",
        },
        "why": (
            "A specific claim contradicted by its own evidence is a sentence a seller would read "
            "and believe. The two routes differ because the strict emit route is the researched "
            "API and a caller asserting a false bound has made a mistake, while the interaction "
            "route is being told a fact about a buyer, and 40% of a video is not a caller error."
        ),
        "change_it": "check_indicators and interact in dsr/signals/emission.py and engine.py.",
        "blast_radius": "Which signals exist.",
    },
    {
        "id": "withdraw-used-registration",
        "topic": "whether a registration that has already emitted signals can be withdrawn",
        "basis": (
            '"Globally installed signals should be considered an immutable API contract with '
            'Salesloft and only additive changes will be allowed." The sentence does not '
            "address retraction, and this reading is stricter than it."
        ),
        "value": {
            "unused": "withdrawable, soft-deleted, still reachable",
            "used": "refused with 409, naming the signal count",
        },
        "why": (
            "Every stored signal names the registration it followed. Hiding that registration "
            "would leave the audit trail pointing at a path the API no longer serves, which is "
            "the exact defect the audit-source rule exists to prevent. A team that needs different "
            "behaviour registers a new signal type, which the one-per-integration rule allows."
        ),
        "change_it": "withdraw in dsr/signals/engine.py.",
        "blast_radius": "The registry listing, and whether a type can be reused.",
    },
    {
        "id": "undeclared-attribution-carries-through",
        "topic": "a signal carrying an attribution value its registration does not declare",
        "basis": (
            "The registration carries an attribution list, so the natural reading is that only "
            "declared values are honoured. The product's standing rule is the opposite direction: "
            "a team adding a field must not need coordination with anyone."
        ),
        "value": {
            "carried": True,
            "warned": True,
            "refused": "only a key outside the researched vocabulary of five",
        },
        "why": (
            "A registration is additive, so a partner who registered person_id and then starts "
            "sending opportunity_id has done something the contract can absorb. Dropping the "
            "signal would lose a buyer's intent over a bookkeeping detail; recording the warning "
            "keeps the gap visible. A key that is not one of the five researched values is "
            "refused, because Salesloft derives the receiver from this object and would not "
            "understand it."
        ),
        "change_it": "normalise_attribution in dsr/signals/emission.py.",
        "blast_radius": "Which signals are accepted, and the warnings carried on them.",
    },
    {
        "id": "amendment-invalidation-test",
        "topic": 'how "only additive changes will be allowed" became a decision',
        "basis": (
            "The sentence states the rule and nothing about how to apply it. This entry records "
            "the reading, because a rule with no test is a slogan."
        ),
        "value": {
            "test": "an amendment may only add. It may not remove, and it may not tighten.",
            "patches_are": (
                "additions to the declared sets, not replacements of them, so a PATCH naming one "
                "indicator adds that indicator. There is no way to express a removal here at all."
            ),
            "allowed": [
                "add an indicator",
                "add a locale",
                "add an attribution type",
                "add an optional data_shape property",
                "change broadcast_notification, which is a default for future signals",
            ],
            "refused": [
                "change type, signal_name or integration_id",
                "rewrite an existing shape, including an indicator's metadata_shape",
                "rewrite the text of a locale that already exists",
                "add a data_shape.required name (tightening: it breaks conforming signals)",
                "remove a data_shape.required name (a removal, which the rule does not permit)",
            ],
        },
        "why": (
            "Two things fell out of taking the sentence literally rather than inventing a "
            "narrower rule. First, a patch is a set of additions, so removal is inexpressible and "
            "therefore cannot happen by accident - which is worth more than a cleverer test that "
            "has to guess whether a caller meant to replace a list. Second, data_shape.required is "
            "frozen in both directions: growing it breaks signals that were already valid, and "
            "shrinking it is a removal. To require something new, register a new signal type, which "
            "the one-per-integration rule allows."
        ),
        "change_it": "amendment_findings in dsr/signals/registration.py.",
        "blast_radius": "Every PATCH to a registration.",
    },
    {
        "id": "duplicate-idempotency-key-is-not-an-error",
        "topic": "what a repeated idempotency_key returns",
        "basis": (
            '"If we receive two signals with the same idempotency_key one of them will be '
            'dropped. The first one wins." A dropped signal is not a failed request.'
        ),
        "value": {
            "status": 200,
            "outcome": "dropped",
            "returns": "the signal that was kept, with duplicate_attempts incremented",
            "checked_after_validation": True,
        },
        "why": (
            "Answering 4xx would tell a sender its signal was rejected, when in fact the first "
            "one succeeded. The increment is the visible half of the rule: a sender that retried "
            "can see that the retry landed and was ignored, and the increment is an audited write "
            "naming the route that made it. Validation runs first so a malformed retry cannot "
            "occupy a key and then block the real signal behind it."
        ),
        "change_it": "emit in dsr/signals/engine.py.",
        "blast_radius": "The emit response shape, and the duplicates_dropped count.",
    },
    {
        "id": "render-on-read",
        "topic": "when the Live Feed sentence is rendered",
        "basis": "No sourced statement about when rendering happens.",
        "value": {
            "when": "on read",
            "stored": "the raw data, the indicators, and the warnings",
            "re_rendered_if": "an amendment adds a locale",
        },
        "why": (
            "A registration is immutable in everything that changes a sentence, so recomputing "
            "gives the same answer for every sentence already read - and it means an amendment "
            "that adds a locale shows up in signals emitted before that locale existed, which a "
            "frozen render would hide."
        ),
        "change_it": "render_signal in dsr/signals/feed.py.",
        "blast_radius": "Every read of a signal or a feed row, and its cost.",
    },
    {
        "id": "room-scope-is-a-filter",
        "topic": "what a room-scoped listing guarantees",
        "basis": (
            "The research describes a DSR as the place a buyer is looking, and says nothing about "
            "access control."
        ),
        "value": {
            "room_scoped_paths": "a listing or an emission under /rooms/{room_id}/...",
            "guarantee": "a filter over the room_id column, not an authorisation check",
        },
        "why": (
            "This product's rooms carry no ACL. Writing a query that looked like it enforced one "
            "would be a claim the store cannot back up, so the scope is described as what it is."
        ),
        "change_it": "signals and live_feed in dsr/signals/engine.py.",
        "blast_radius": "Every room-scoped read and write.",
    },
    {
        "id": "actionability-is-never-set-by-the-sender",
        "topic": "whether a signal can claim to be actionable",
        "basis": (
            '"It is important to note that users may choose to not take action on a signal. '
            "Actionability depends on the end user's governance (Play) configurations and "
            'settings within Salesloft."'
        ),
        "value": {
            "actionable": False,
            "on_every_signal": True,
            "on_every_feed_row": True,
            "rejected_from_payload": True,
            "note": ACTIONABILITY_NOTE,
        },
        "why": (
            "The field is stored as false rather than omitted, so a reader never has to guess "
            'whether absence meant "no" or "this product does not know". It is refused in the '
            "payload because a sender that could set it would be able to promise a seller a task "
            "this product does not create."
        ),
        "change_it": (
            "RESERVED_EMISSION_FIELDS and normalise_emission in dsr/signals/emission.py, and the "
            "summary and feed payloads in engine.py."
        ),
        "blast_radius": "Every signal and every feed row.",
    },
    {
        "id": "plays-are-not-built-here",
        "topic": "why there is no Play registration in this workflow",
        "basis": (
            "The research's own automation note says actionability is governed by the end user's "
            "Play configuration. Registering Plays is section 13 of the same research file, which "
            "is a separate workflow (WF-013) with its own brief."
        ),
        "value": {"plays": "not implemented", "reason": "a different researched workflow"},
        "why": (
            "Building it here would implement another workflow's specification inside this one, "
            "and would make this feature's audit log name Plays that the page above it never "
            "registered. The research only ever mentions Plays as the end user's own "
            "configuration."
        ),
        "change_it": "Nothing to revert; this entry exists so the absence reads as a decision.",
        "blast_radius": "Nothing in this feature.",
    },
    {
        "id": "json-schema-subset",
        "topic": "the boundary of the data_shape validator",
        "basis": (
            "The research says data_shape and metadata_shape are JSON Schema, without naming a "
            "draft or a keyword set. This is a boundary, not a judgement call, and it is listed "
            "so a partner authoring a shape can see it before they hit it."
        ),
        "value": {
            "supported": sorted(schema.SUPPORTED_KEYWORDS),
            "not_supported": [
                "$ref",
                "$defs",
                "allOf",
                "anyOf",
                "oneOf",
                "not",
                "if/then/else",
                "patternProperties",
                "dependentSchemas",
                "non-boolean additionalProperties",
            ],
            "unknown_keywords": "ignored, as JSON Schema requires of an annotation",
            "extra_fields": (
                "a signal may carry more than data_shape declares, unless the shape sets "
                "additionalProperties to false. A registration authored before a later release "
                "invented a field must not reject that field."
            ),
        },
        "why": (
            "The alternative - requiring a JSON-Schema library the project does not depend on - "
            "buys draft coverage this product does not need and costs a dependency every "
            "deployment has to satisfy. The implemented subset is small enough to read in one "
            "sitting, which is the property a reviewer actually needs."
        ),
        "change_it": "dsr/signals/schema.py, and SUPPORTED_KEYWORDS which the vocabulary endpoint serves.",
        "blast_radius": "Every data and metadata validation.",
    },
    {
        "id": "icu-subset-and-apostrophes",
        "topic": "which ICU Message forms are implemented, and the apostrophe rule",
        "basis": (
            "The research names ICU Messages and gives one worked example using a simple argument "
            "and a plural with =1/other and #."
        ),
        "value": {
            "implemented": list(icume.SUPPORTED_ARGUMENT_TYPES)
            + ["simple argument", "=N exact plural branches", "#"],
            "not_implemented": ["number", "date", "time", "apostrophe escaping"],
            "apostrophe": "treated as an ordinary character",
            "plural_categories": "English only: zero, one, other, with exact matches checked first",
            "missing_argument": "renders the placeholder and adds a warning; never raises",
        },
        "why": (
            "Real ICU uses a single quote to introduce a quoted literal, which means an English "
            "contraction in a description silently swallows the rest of the sentence unless the "
            "author knows the rule. Registrations here are written by a partner in plain prose, "
            "so treating the apostrophe as ordinary renders what its author meant. A missing "
            "argument degrades the sentence and is reported rather than raised, because a "
            "registration one word short should not remove a buyer's intent from a seller's feed."
        ),
        "change_it": "dsr/signals/icume.py.",
        "blast_radius": "Every rendered sentence.",
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
    reader can see where the line falls, and showing the sourced vocabulary next
    to the inferred behaviour is what lets them.
    """
    return {
        "count": len(INFERENCES),
        "sourced_quote": SOURCED_QUOTE,
        "sourced": {
            "urgencies": list(URGENCIES),
            "attribution_types": list(ATTRIBUTION_TYPES),
            "indicator_quality_examples": indicators.describe_vocabulary()["quality_examples"],
            "actionability_note": ACTIONABILITY_NOTE,
        },
        "locale_fallback_example": {
            "requested": "de-AT",
            "declared": {"en": "…", "en-GB": "…"},
            "resolves_to": resolve_locale("de-AT", {"en": "…", "en-GB": "…"})[0],
        },
        "inferences": [dict(entry) for entry in INFERENCES],
    }
