"""Every judgement call in this package, in one inspectable place.

The research for WF-028 is explicit about most of the mechanism: it gives the Play
request body field by field, names the three task types, states the assignment
precedence and the Account fallback in a quoted table row, says a registered Play
must be enabled before it does anything, allows more than one framework per signal
registration, and states the webhook retry policy to the second.

What it does *not* do is say how a registrant behaves on the edges of that
description, and the edges are where a build has to decide something. Those
decisions are collected here rather than left as comments in function bodies,
because a judgement call in a comment is one nobody re-reads and a wrong one
becomes product behaviour without anyone noticing. Each entry is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``GET /api/wf-028/inferences``, so a
  reviewer reads the list instead of reconstructing it from a diff.

The three that matter most, in order of how much damage getting them wrong would do:

* :data:`SOURCED_QUOTE`'s companion, ``registration-is-not-activation`` - a Play
  that fires on registration puts tasks in sellers' queues that nobody asked for.
* ``buyer-engagement-score-is-supplied`` - the research's own gaps appendix records
  that the score is referenced but never defined, so this package orders by it and
  never computes it.
* ``assignment-may-be-unresolved`` - a task with nobody to assign it to is still a
  task, and dropping it would lose the evidence that the automation fired.
"""

from __future__ import annotations

from typing import Any

from dsr.plays.assignment import RULES as ASSIGNMENT_RULES
from dsr.plays.matching import MATCH_REASONS
from dsr.plays.vocabulary import (
    ACTIVATION_PATH,
    ALL_ATTRIBUTE_KEYS,
    ATTRIBUTE_KEYS,
    ENGAGEMENT_WINDOW_DAYS,
    PLAY_EVENT_TYPES,
    SUPPORTED_DYNAMIC_FIELDS,
    TASK_TYPES,
    UNSOURCED_ATTRIBUTE_KEYS,
    WEBHOOK_RETRY_ATTEMPTS,
    WEBHOOK_RETRY_SPACING_SECONDS,
)

#: The sentence from the research that governs the shape of everything below: the
#: fields are named, the behaviour on their edges is not.
SOURCED_QUOTE = (
    "After registering a signal (see #12), register a Play: POST "
    "https://api.salesloft.com/v2/integrations/signals/registrations/plays with "
    "signal_registration_id, localized name/label/description, the indicators[] that "
    "should trigger it, and attributes (task_type: call | email | add-to-cadence, "
    "task_subject, task_reminder_hours, email_subject, email_template)."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "registration-is-not-activation",
        "topic": "whether a Play creates tasks the moment it is registered",
        "basis": (
            'The research separates the two moments explicitly: "After registration, the '
            "registered Play must be enabled in the Salesloft UI. You can do so by going to "
            'Settings -> Workflow -> Plays -> Edit Play." It says the Play must be enabled, '
            "which is only a requirement if registration does not enable it."
        ),
        "value": {
            "enabled_on_registration": False,
            "activation_path": ACTIVATION_PATH,
            "only_way_to_change": "the enable and disable routes",
        },
        "why": (
            "This is the one bug this feature cannot have. A Play that fires on registration "
            "puts a task in a seller's queue that nobody asked for, and the seller has no "
            "way to trace it back to the signal that caused it. Registration writes "
            "enabled: false and nothing else in the codebase may set it, so there is exactly "
            "one door and it is not the register route."
        ),
        "change_it": "dsr/plays/framework.py writes the default; dsr/plays/activation.py owns every change.",
        "blast_radius": (
            "Whether any task exists at all. A change here turns every registered Play into a "
            "live automation, which is not something to do for convenience."
        ),
    },
    {
        "id": "buyer-engagement-score-is-supplied",
        "topic": "where the Buyer Engagement Score that decides task assignment comes from",
        "basis": (
            'The researched assignment table says "The most engaged Person on the Account in '
            "the Last 30 days (Highest Buyer Engagement Score)\", and the research's own gaps "
            "appendix records: \"Salesloft references a 'Buyer Engagement Score' as a "
            'tie-breaker for task assignment but does not document how it is computed." The '
            "score's purpose and its window are sourced; its formula is a documented gap."
        ),
        "value": {
            "computed_here": False,
            "supplied_as": "engagement_score on each candidate person",
            "window_days": ENGAGEMENT_WINDOW_DAYS,
            "candidate_source": [
                "the dispatch request's candidates",
                "the signal's candidates or engagement_roster",
                "the room record's engagement_roster",
            ],
        },
        "why": (
            "A formula invented here would be a plausible-looking number that is not the "
            "vendor's number, and it would decide which seller gets a task. Ordering by a "
            "supplied score and refusing to guess is the only reading the evidence supports. "
            "A candidate inside the window with no score is skipped and reported in notes, "
            "never silently treated as zero."
        ),
        "change_it": "dsr/plays/assignment.py::_resolve_account",
        "blast_radius": "Every task assigned through the Account branch of the precedence.",
    },
    {
        "id": "assignment-may-be-unresolved",
        "topic": "what happens to a task whose assignment cannot be resolved",
        "basis": (
            'The research says Salesloft "creates the task ... and assigns it", so creation '
            "and assignment are two steps. It does not say what a task is when the assignment "
            "fails, and the four objects it names are Person, Account, User and Content - "
            "records this product does not hold."
        ),
        "value": {
            "created_anyway": True,
            "assigned": False,
            "reason_field": "assignment.rule",
            "reasons": list(ASSIGNMENT_RULES),
        },
        "why": (
            "Dropping the task would lose the evidence that the Play fired, which is the one "
            "thing a reviewer needs when a seller's queue is not what they expected. An "
            "unassigned task that names its object and its reason is diagnosable; a missing "
            "task is not. The caller can fill it in by passing subjects for the object id."
        ),
        "change_it": "dsr/plays/assignment.py::resolve_assignment",
        "blast_radius": "The unassigned count in the summary and the routable flag on a task.",
    },
    {
        "id": "task-type-requires-its-own-subject",
        "topic": "which subject attribute each task type must carry",
        "basis": (
            "The researched attributes list carries task_subject and email_subject as two "
            "separate members, and names three task types. The research does not say which "
            "type needs which."
        ),
        "value": {
            "call": "task_subject",
            "add-to-cadence": "task_subject",
            "email": "email_subject",
        },
        "why": (
            "Two members that no type distinguishes would be one member. A call with no "
            "subject is a task titled nothing, and a seller looking at a queue of them cannot "
            "tell which buyer they are for. Requiring the type's own subject is the reading "
            "the field list supports best."
        ),
        "change_it": "TASK_TYPE_SUBJECT_ATTRIBUTE in dsr/plays/vocabulary.py",
        "blast_radius": "Which Play bodies are accepted at registration.",
    },
    {
        "id": "cadence-identifier-is-unsourced",
        "topic": "how a Play knows which cadence to add a buyer to",
        "basis": (
            '"These are the available Play task types: Call, Email, Add Person to a Cadence." '
            "The researched attributes list is task_type, task_subject, task_reminder_hours, "
            "email_subject, email_template. None of them identifies a cadence."
        ),
        "value": {
            "key": "cadence_id",
            "required": False,
            "sourced": False,
            "absent_behaviour": "the Play registers, warns, and its tasks report routable: false",
        },
        "why": (
            "One of the three researched task types would be permanently unusable if a "
            "required field were invented, and pretending the gap is not there would be "
            "worse. The key is accepted, published with sourced: false, warned about when "
            "absent, and a task without it says so rather than going nowhere quietly."
        ),
        "change_it": (
            "UNSOURCED_ATTRIBUTE_KEYS in dsr/plays/vocabulary.py; "
            "TASK_TYPE_REQUIRES in dsr/plays/tasks.py"
        ),
        "blast_radius": "The routable flag on a cadence task, and the no_cadence_named warning.",
    },
    {
        "id": "dynamic-fields-are-checked-by-placement-not-by-name",
        "topic": "where a dynamic field is allowed, and which ones",
        "basis": (
            '"At this time, Dynamic Fields are not supported outside of email templates. The '
            'only exception here is that task_subject supports name." The sentence states a '
            "placement rule and names one field; it does not enumerate the fields an email "
            "template may use."
        ),
        "value": {
            "supported_in": "attributes.email_template",
            "exempt": "attributes.task_subject",
            "supported_names": list(SUPPORTED_DYNAMIC_FIELDS),
            "elsewhere": "refused",
        },
        "why": (
            "The rule is about where, so it is enforced as a rule about where: no name is "
            "checked inside an email template, only name is allowed in task_subject, and any "
            "field anywhere else is refused. Refusing rather than storing an unrenderable "
            "field is the point - a field that renders empty in a seller's task list is a "
            "sentence with a hole in it."
        ),
        "change_it": "_check_dynamic_fields in dsr/plays/framework.py",
        "blast_radius": "Which attribute bodies are accepted at registration.",
    },
    {
        "id": "destroy-requires-disable",
        "topic": "whether an enabled Play can be destroyed outright",
        "basis": (
            'The researched endpoints include ".../plays/{id} (update/destroy)". The '
            "research says nothing about destroying a live Play, and separately says a "
            "registered Play must be enabled for it to do anything."
        ),
        "value": {
            "destroy_while_enabled": "refused with 409",
            "route_order": "disable, then destroy",
        },
        "why": (
            "An enabled Play is a running automation that creates tasks with no human in the "
            "loop. Removing it while it runs is not something to do by accident, and the "
            "sensible sequence - switch it off, then retire it - is one request longer and "
            "leaves an audit row saying when the switch was thrown."
        ),
        "change_it": "PlayEngine.destroy in dsr/plays/engine.py",
        "blast_radius": "Whether a registry entry can be removed.",
    },
    {
        "id": "frozen-while-live",
        "topic": "which fields of a Play can be changed once it has created a task",
        "basis": (
            "The research gives Plays an update endpoint and states no constraint on it. Its "
            'sibling constraint - "Globally installed signals ... only additive changes will '
            'be allowed" - is about signal registrations, not Plays, and is not borrowed here.'
        ),
        "value": {
            "frozen_when_tasks_exist": [
                "indicators",
                "attributes",
                "the text of an existing locale on name, label, description",
            ],
            "always_refused": ["signal_registration_id", "enabled", "enabled_at", "enabled_by"],
            "always_allowed": ["adding a locale"],
        },
        "why": (
            "One rule rather than several: a Play that has already produced a task has a "
            "history, and the fields that decide what fires and what is created are part of "
            "it. Adding a locale stays allowed because it cannot invalidate a task that "
            "already fired, and removing one is refused always because a sentence that "
            "existed is gone. Registration id is identity and the activation fields belong "
            "to their own routes."
        ),
        "change_it": "amendment_findings in dsr/plays/framework.py",
        "blast_radius": "Which PATCH bodies are accepted.",
    },
    {
        "id": "registration-must-exist",
        "topic": "whether a Play can be registered against a signal registration that does not exist",
        "basis": (
            '"After registering a signal (see #12), register a Play" states an order, and '
            '"When a matching signal arrives, Salesloft creates the task" means the signals '
            "that arrive follow a registration. The research does not say what a Play naming "
            "no registration does."
        ),
        "value": {"refused_with": 409, "code": "signal_registration_not_found"},
        "why": (
            "A Play whose registration is absent can never fire, and it would sit in the "
            "registry looking configured. The alternative - storing it as a template waiting "
            "for a registration - is how a page ends up listing automations that will never "
            "run. Every Play's indicator list is also checked against that registration's "
            "declared indicators, which is the same reason."
        ),
        "change_it": "PlayEngine.register in dsr/plays/engine.py",
        "blast_radius": "Which Play bodies are accepted, and the undeclared-trigger refusal.",
    },
    {
        "id": "strict-attribute-set",
        "topic": "whether an attribute outside the researched list is refused or stored",
        "basis": (
            "The research enumerates the attributes of a Play body. This product's wider rule "
            "is that payloads are arbitrary JSON and a team adding a field must not need "
            "coordination, which points the other way."
        ),
        "value": {
            "accepted": list(ALL_ATTRIBUTE_KEYS),
            "sourced": list(ATTRIBUTE_KEYS),
            "unsourced": list(UNSOURCED_ATTRIBUTE_KEYS),
        },
        "why": (
            "The two rules are not actually in conflict, and the distinction is worth stating: "
            "storage is open, and a *vendor contract* is checked. A Play body is an API "
            "contract with an enumerated member list, so a typo in task_reminder_hours should "
            "be visible at registration rather than stored and ignored forever. A team that "
            "needs a new member adds it to this package's vocabulary - one file, no migration, "
            "and the acceptance test that the research quotes still holds."
        ),
        "change_it": "ALL_ATTRIBUTE_KEYS in dsr/plays/vocabulary.py",
        "blast_radius": "Which Play bodies are accepted.",
    },
    {
        "id": "signal-matches-on-type",
        "topic": "how a Play is matched to a signal that names no registration id",
        "basis": (
            "The researched Play body is keyed on signal_registration_id. A researched signal "
            "carries a type and an attribution, and its registration is resolved by that type "
            "rather than carried as an id."
        ),
        "value": {"fallback": "the signal's type, which is the key a registration is filed under"},
        "why": (
            "Without the fallback an inline signal would match nothing, and the only way to "
            "prove a Play fires would be to post a signal to this product first. The fallback "
            "is narrow: it is only used when the signal names no registration, and a Play "
            "whose registration genuinely differs is still reported as "
            "registration_mismatch."
        ),
        "change_it": "match_plays in dsr/plays/matching.py",
        "blast_radius": "Which Plays an inline signal can fire.",
    },
    {
        "id": "one-off-is-enforced-per-play-and-signal",
        "topic": "what stops a repeated signal creating a second task",
        "basis": (
            '"A Play is an automation that generates a one-off action in response to an '
            'internal or external signal." The researched first-one-wins rule is stated for '
            'signals ("If we receive two signals with the same idempotency_key one of them '
            'will be dropped"), which happens where the signal is emitted.'
        ),
        "value": {
            "dedupe_key": "play_id + the signal's record id or idempotency_key",
            "on_repeat": "the existing task is reported and its duplicate count incremented",
        },
        "why": (
            "A Play that creates a task per arrival is not a one-off action, and relying on a "
            "sender being careful is a worse guarantee than a check. The repeat is counted on "
            "the task so a sender that retried can see the retry landed and was ignored, and "
            "the write is audited against the route that made it."
        ),
        "change_it": "PlayEngine.dispatch in dsr/plays/engine.py",
        "blast_radius": "The number of tasks a Play can hold for one signal.",
    },
    {
        "id": "delivery-success-is-2xx",
        "topic": "what counts as a delivered webhook",
        "basis": (
            '"A failing webhook is retried three additional times, spaced 15 seconds apart, '
            'before being marked as failed." The sentence does not define failing.'
        ),
        "value": {
            "delivered": "2xx",
            "ok_may_be_stated": True,
            "conflict": "refused; the status code decides",
        },
        "why": (
            "A 4xx will not become a 2xx on a retry, so treating it as retryable spends the "
            "schedule on a delivery that cannot succeed. A body claiming ok while carrying a "
            "500 is a caller bug, and believing either one silently is how a failed webhook "
            "ends up marked delivered."
        ),
        "change_it": "attempt_ok in dsr/plays/events.py",
        "blast_radius": "Every delivery state.",
    },
    {
        "id": "retry-spacing-is-enforced",
        "topic": "whether an early retry attempt is accepted",
        "basis": '"spaced 15 seconds apart" is a stated spacing, and a fixed one rather than a backoff.',
        "value": {
            "additional_attempts": WEBHOOK_RETRY_ATTEMPTS,
            "spacing_seconds": WEBHOOK_RETRY_SPACING_SECONDS,
            "early_attempt": "refused, naming the time it is due",
        },
        "why": (
            "The researched number is only true if nothing records an attempt early. "
            "Reporting the schedule without enforcing it would let a page show '3 additional "
            "attempts, 15s apart' next to an attempt history that says otherwise."
        ),
        "change_it": "due() in dsr/plays/events.py, called by PlayEngine.record_attempt",
        "blast_radius": "Every recorded delivery attempt.",
    },
    {
        "id": "account-tie-break",
        "topic": "how two people on one Account with the same engagement score are ordered",
        "basis": '"Highest Buyer Engagement Score" names a maximum and says nothing about a tie.',
        "value": {"order": ["most recent engagement", "lowest person id"]},
        "why": (
            "Without a tie-break the same Account on the same day can resolve to two different "
            "sellers, and a disagreement nobody can explain is worse than an arbitrary but "
            "stated rule. Both keys are checked in the open and the tie is reported in notes."
        ),
        "change_it": "the sort key in _resolve_account, dsr/plays/assignment.py",
        "blast_radius": "Which person an Account-assigned task is related to.",
    },
    {
        "id": "one-off-task-needs-a-signal-identity",
        "topic": "what a dispatch requires of the signal it is about",
        "basis": (
            'The researched signal carries idempotency_key, and "the first one wins" is what '
            "makes a repeated signal a fact rather than an event. A Play's one-off property is "
            "stated but no key is named for it."
        ),
        "value": {
            "required": ["a stored signal's record id", "an inline signal's idempotency_key"]
        },
        "why": (
            "Without either there is no way to tell a new signal from a repeat, and a Play that "
            "cannot tell the difference creates a task for both. Refusing is better than "
            "defaulting to a fresh key every time, which would make the one-off guard "
            "unreachable."
        ),
        "change_it": "PlayEngine._signal_key in dsr/plays/engine.py",
        "blast_radius": "Which dispatches are accepted at all.",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return entry
    return None


def describe() -> dict[str, Any]:
    """The whole register, served so a reviewer reads it instead of the diff."""
    return {
        "sourced_quote": SOURCED_QUOTE,
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "published_values": {
            "task_types": list(TASK_TYPES),
            "play_event_types": list(PLAY_EVENT_TYPES),
            "engagement_window_days": ENGAGEMENT_WINDOW_DAYS,
            "webhook_retry_additional_attempts": WEBHOOK_RETRY_ATTEMPTS,
            "webhook_retry_spacing_seconds": WEBHOOK_RETRY_SPACING_SECONDS,
            "activation_path": ACTIVATION_PATH,
            "sourced_attributes": list(ATTRIBUTE_KEYS),
            "unsourced_attributes": list(UNSOURCED_ATTRIBUTE_KEYS),
        },
        "match_reasons": MATCH_REASONS,
        "assignment_rules": ASSIGNMENT_RULES,
    }
