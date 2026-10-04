"""Every judgement call WF-078 made, with the alternative it rejected.

The specification's own instruction to an implementer is that one "must derive it and
record the derivation, not assume it". This module is that record.

Each entry names the open question, the evidence that left it open, the options, the one
this build took, and - the part that matters - what the rejected options would have cost.
A derivation with no rejected alternative recorded is a guess wearing a derivation's
clothes, and a reviewer cannot tell the two apart.

Three of these are not judgements this build made at all. They are the three data sources
the specification itself marks ``[inferred]``, and they are recorded as assumptions rather
than derivations because a reader who does not know they were assumed cannot discount the
right part of an answer.

The HTTP layer serves this table at ``GET /api/wf-078/decisions`` so the record is
readable by whoever reviews the feature, rather than buried in a docstring that nobody
opens. ``GET /api/wf-078/decisions/{id}`` returns one.
"""

from __future__ import annotations

from typing import Any

DECISIONS: dict[str, dict[str, Any]] = {
    "OWNERSHIP_WF078_OWNS_THE_GATE": {
        "question": (
            "The e-signature workflow and this workflow both describe a recipient identity "
            "verification object. Which one owns the setting, so the gate is not implemented "
            "twice?"
        ),
        "left_open_by": (
            "The specification names the collision without resolving it. It records that 'the "
            "two specs also name the same verification object' and that 'the e-signature "
            "workflow's evidence states that identity verification is enabled per-workspace "
            "(passcode, SMS, KBA, ID check)', then instructs: 'Decide which workflow owns the "
            "setting and which reads it, and record the decision, so one gate is not "
            "implemented twice.'"
        ),
        "options": {
            "wf078_owns_gate": (
                "This workflow owns the verification settings object, the two axes, the "
                "validation, the attempt evaluation and the withholding of the body. The "
                "e-signature workflow reads the setting and sends the request."
            ),
            "wf095_owns_gate": (
                "The e-signature workflow owns it because it owns the document and recipient "
                "records the setting lives on, and this workflow becomes a read-only reporter."
            ),
            "shared_rule_module": (
                "The rule moves into a module both workflows import, so neither ticket owns "
                "it alone."
            ),
            "wf078_owns_gate_workflow_keyed": (
                "This workflow owns the gate, and the setting is keyed per workflow rather "
                "than per workspace."
            ),
        },
        "chosen": "wf078_owns_gate",
        "rejected_because": (
            "Delegating enforcement to WF-095 was rejected on evidence: WF-095 is not "
            "implemented in this repository, so a design that hands it the gate cannot be "
            "tested, cannot be reviewed, and would leave this workflow with a setting it reads "
            "and never enforces. A shared rule module was rejected because the module would "
            "have to live in a package one of the two tickets owns, which is the same collision "
            "with one more indirection in front of it. Workflow-keyed settings were rejected "
            "because the specification's own example has one recipient verified for viewing and "
            "differently for signing within one document, and a per-workflow key makes that two "
            "settings to keep in step rather than two gates on one recipient."
        ),
        "cost_of_the_choice": (
            "This workflow now owns a recipient-shaped object that the e-signature workflow "
            "will one day want to write. The mitigation is structural rather than procedural: "
            "the setting is ordinary JSON in records.data with no typed column, so the other "
            "workflow writes it through the same schema-flexible store without a migration and "
            "without a shared file. The collections are namespaced wf078_, so a reader looking "
            "for a recipient has to know which workflow wrote it, and the page says whose count "
            "it is showing."
        ),
        "jev_audit_id": "jev-20261004T140557-22596-57651",
    },
    "TWO_AXES_NOT_ONE": {
        "question": (
            "Is a recipient's verification one method at one moment, or a method per moment?"
        ),
        "left_open_by": (
            "The specification's timing table gives each gate a place, and its extensibility "
            "note says the method is 'a discriminated union on the recipient (passcode / phone "
            "/ KBA / ID) with an independent timing axis (before_open vs before_sign) - the "
            "same recipient object can be verified differently for viewing and signing'. It does "
            "not give a storage shape."
        ),
        "options": {
            "method_and_moment_on_the_recipient": (
                "One method and one moment per recipient. The simplest shape that fits a form."
            ),
            "gate_keyed_mapping": (
                "A mapping keyed by gate, each entry a method and its own value, so one "
                "recipient can carry two."
            ),
            "one_recipient_row_per_gate": (
                "A separate recipient row per gate, so each row carries one method and one moment."
            ),
        },
        "chosen": "gate_keyed_mapping",
        "rejected_because": (
            "One method and one moment per recipient cannot express the case the "
            "specification names: 'the same recipient object can be verified differently for "
            "viewing and signing'. Choosing it would make a passcode before open plus a "
            "knowledge-based check before sign unrepresentable, and the only way to get there "
            "would be to overwrite the first with the second, which is the defect the "
            "extensibility note is warning about. One recipient row per gate was rejected "
            "because two rows means two names, two email addresses and two roles to keep "
            "aligned, and they would drift: a sender who changed the role on one row would "
            "leave the other row signing under a role it no longer has."
        ),
        "cost_of_the_choice": (
            "A setting is nested one level deeper than a flat field, so a caller posting a "
            "recipient writes {'verification_settings': {'before_open': {...}}} rather than "
            "{'method': 'passcode', 'passcode': '...'} at the top level. The page reads the "
            "nested shape directly, and the endpoint accepts a flat entry too, so a caller that "
            "does not care about the distinction never has to learn it."
        ),
    },
    "DERIVED_RECIPIENT_ROLE": {
        "question": "What word names a recipient who is expected to sign?",
        "left_open_by": (
            "The audience column of the timing table says 'Signers only' and the other row "
            "says 'All recipients'. The specification names no role vocabulary, so the word "
            "for a recipient who signs is this build's to choose."
        ),
        "options": {
            "two_roles": "A recipient is either a signer or a plain recipient.",
            "a_signing_boolean": "One boolean, is_signer, on the recipient.",
            "no_role_at_all": "No role, and before_sign applies to whoever the document "
            "names as a signer elsewhere.",
        },
        "chosen": "two_roles",
        "rejected_because": (
            "No role at all was rejected because the rule this workflow most needs to "
            "enforce - that a before_sign gate applies to signers only - has nothing to read. "
            "A boolean was rejected because it is the same two states wearing one word, and "
            "the word is the part a reviewer and a page both want. Two named roles also leave "
            "the door open for a third, which a boolean cannot express without becoming a "
            "bitfield."
        ),
        "cost_of_the_choice": (
            "A role this workflow does not know is treated as not-a-signer, so a misspelled "
            "role cannot quietly install a before_sign gate on somebody who cannot clear it. "
            "That is the deliberate direction of the default: the wrong reading of an unknown "
            "role fails closed rather than open."
        ),
    },
    "DERIVED_E164_PRECISE": {
        "question": "How exactly is 'international format' read?",
        "left_open_by": (
            "The specification quotes one example and no rule: 'must be in international "
            "format (e.g., +1555667890)'."
        ),
        "options": {
            "plus_and_digits": "A plus, then digits, with a length bound.",
            "full_e164_strict": ("The full E.164 grammar, including a country-code length table."),
            "accept_anything_with_a_plus": "A leading plus is enough.",
        },
        "chosen": "plus_and_digits",
        "rejected_because": (
            "A leading plus alone would accept '+0123456789', which no country can dial, so "
            "a code sent there goes nowhere and the recipient is locked out of a document "
            "they are entitled to open. The full E.164 grammar would be more correct and is "
            "rejected because the specification cites one example and no grammar: importing a "
            "country-code table the source does not mention would enforce a rule no cited "
            "document states. What the source does state is a plus and a country code, so a "
            "plus, a non-zero first digit and 8 to 15 digits is the whole of the sourced rule."
        ),
        "cost_of_the_choice": (
            "A number with a syntactically valid but unallocated country code passes "
            "configuration and fails at delivery. That is recorded rather than hidden: a code "
            "this workflow sent but that never arrived writes a failed attempt with a named "
            "reason, so the gap is visible in the trail rather than silent."
        ),
    },
    "DERIVED_SMS_CODE_IS_THE_ROOM'S": {
        "question": "Where does the one-time code come from?",
        "left_open_by": (
            "The specification says the signer 'can select the Send code button to receive a "
            "6-digit code via text message' and can 'request the code to be sent again if "
            "needed'. It names no gateway and no sender."
        ),
        "options": {
            "the_room_generates_it": (
                "This room generates the six-digit code, holds it against the recipient, and "
                "the send is a recorded event."
            ),
            "a_gateway_generates_it": (
                "An SMS gateway generates and sends the code, and this room only records the "
                "outcome."
            ),
            "no_delivery_step": "The recipient supplies the code they were sent offline.",
        },
        "chosen": "the_room_generates_it",
        "rejected_because": (
            "A gateway was rejected because no gateway is named in any cited source, and "
            "wiring one would put a vendor in the middle of the one flow the specification "
            "insists this room owns: 'you are solely responsible for making sure that your "
            "signer/end user authentication process is sufficient'. No delivery step was "
            "rejected because the evidence describes a send the recipient triggers, and "
            "dropping it would make the gate untestable end to end."
        ),
        "cost_of_the_choice": (
            "This room holds the code and must protect it. The code is generated with "
            "secrets, compared with compare_digest, is never returned by the prompt endpoint, "
            "and is replaced on every resend so a superseded code stops working immediately. "
            "The real gateway is a delivery concern that does not change the gate."
        ),
    },
    "DERIVED_SMS_RESEND": {
        "question": "Is there a cap on how many times a code can be re-sent?",
        "left_open_by": (
            "The evidence says only that a recipient 'can request the code to be sent again if "
            "needed'. It names no cap, and this workflow has no rate limiter to inherit one "
            "from."
        ),
        "options": {
            "unlimited": "Every resend issues a new code and records it.",
            "a_fixed_cap": "A cap, and the request past it is refused.",
            "a_cooldown": "A cooldown between sends.",
        },
        "chosen": "unlimited",
        "rejected_because": (
            "A cap was rejected because no number for it is sourced, and a guessed cap that "
            "silently refuses a legitimate recipient trades a real failure - a signer locked "
            "out of a document they are entitled to sign - for a hypothetical one. A cooldown "
            "was rejected for the same reason and because it would need a clock this module "
            "deliberately does not read."
        ),
        "cost_of_the_choice": (
            "A recipient can request codes without bound, and each one is a recorded event "
            "carrying its own audit code, so a flood is visible in the trail as a burst of "
            "rows rather than as an absence of them. Each resend supersedes the previous code, "
            "so a flood cannot widen access: it can only narrow it, because at most one code "
            "is ever valid."
        ),
    },
    "DERIVED_KBA_QUESTION_COUNT": {
        "question": "How many knowledge-based questions does a sender record?",
        "left_open_by": (
            "The specification says 'the recipient answers identity questions generated from "
            "public records' and names no count."
        ),
        "options": {
            "one_to_ten": "At least one, at most ten.",
            "exactly_three": "Exactly three.",
            "no_bound": "As many as the sender likes.",
        },
        "chosen": "one_to_ten",
        "rejected_because": (
            "Exactly three was rejected because no source says three, and a sender with two "
            "good questions would be refused for a reason they cannot look up. No bound was "
            "rejected because it makes a request body the only limit on a gate's cost, and "
            "this workflow re-runs the whole check on every attempt, so a recipient list of "
            "five hundred questions is five hundred constant-time comparisons per attempt for "
            "a control nobody asked for."
        ),
        "cost_of_the_choice": (
            "A sender who wants twelve questions must split the recipient or choose a "
            "different method. Ten questions is this build's bound, not the specification's, "
            "and it is one constant in dsr.identity_verification.vocabulary."
        ),
    },
    "DERIVED_CODES_ARE_NOT_MINTED_HERE": {
        "question": "Does this workflow define the integer audit codes for an attempt?",
        "left_open_by": (
            "The specification names the codes - '47 recipient verification with kba passed', "
            "'51 recipient verification with kba failed', '69 recipient verification with "
            "email otp passed', '70 recipient verification with email otp failed' - and "
            "another workflow in this product already owns the whole enum."
        ),
        "options": {
            "mint_a_table_here": (
                "This workflow lays out 47-54 itself, from the method order in the specification."
            ),
            "call_the_owner": (
                "This workflow carries the facts of an attempt and calls "
                "dsr.audit_export.vocabulary.verification_code to turn them into an integer."
            ),
            "carry_no_code": (
                "Record the method and the outcome and let the export derive the code at read time."
            ),
        },
        "chosen": "call_the_owner",
        "rejected_because": (
            "Minting a second table was rejected because the owner already lays out 47-50 as "
            "four pass codes and 51-54 as four fail codes over the same four methods, so two "
            "tables would describe the same integers and the second would drift the moment a "
            "team reordered a method. Carrying no code at all was rejected because the "
            "extension point the owner documents is precisely that a writing feature stamps "
            "event_code onto the record it already writes, and a row with no code is a row a "
            "compliance query cannot find."
        ),
        "cost_of_the_choice": (
            "The engine takes a code_for callable rather than importing the table, so this "
            "package has no import edge to another workflow's package and stays testable on "
            "its own. The coupling is one call site in the feature module and is named in "
            "dsr.identity_verification.vocabulary.CODE_TABLE_OWNER, so a reviewer can find it "
            "without reading the engine."
        ),
    },
    "ASSUMED_KBA_PUBLIC_RECORD_SOURCE": {
        "question": "Where do the identity questions come from?",
        "left_open_by": (
            "The specification marks this itself: 'public-record data source for KBA "
            '[inferred - the docs name kba_verification as "identity questions generated from '
            "public records\" but no vendor]'."
        ),
        "options": {
            "a_public_record_vendor": "Query a vendor for questions about this recipient.",
            "sender_recorded": "The sender records the questions and their expected answers.",
        },
        "chosen": "sender_recorded",
        "rejected_because": (
            "No vendor is named in any cited source, so choosing one would be inventing a "
            "requirement and a dependency from an evidence marker that says the opposite. The "
            "sender records the questions instead, which is the only shape the sourced object "
            "supports: the specification says a recipient 'answers identity questions', which "
            "presupposes somebody wrote them."
        ),
        "cost_of_the_choice": (
            "This workflow does not generate questions and does not hold any public record. A "
            "sender who wants questions generated from public records has to source them "
            "themselves, and the page says so on the panel rather than implying a capability "
            "this build does not have."
        ),
    },
    "ASSUMED_ID_VERIFICATION_PROVIDER": {
        "question": "What checks a government-issued ID here?",
        "left_open_by": (
            "The specification marks this itself: 'ID-verification provider for "
            "id_verification [inferred]'."
        ),
        "options": {
            "a_document_provider": "Send the ID document to a provider and trust its verdict.",
            "compare_recorded_details": (
                "Compare the details a recipient stated against the details a sender recorded."
            ),
            "no_id_method": "Drop the ID method and support three.",
        },
        "chosen": "compare_recorded_details",
        "rejected_because": (
            "A provider was rejected because none is named in any cited source, and a "
            "provider that cannot be named cannot be an owner of the authentication decision "
            "the vendor's own guidance reserves for this room. Dropping the method was "
            "rejected because the method table is sourced and names four methods, and "
            "shipping three would mean shipping a specification with a hole in it."
        ),
        "cost_of_the_choice": (
            "This is the weakest of the four methods and the page says so. It does not read "
            "an ID document, does not check a document is genuine, and does not run a "
            "background check. Every response carries NOT_PROOF so a pass is never read as "
            "more than it is."
        ),
    },
    "ASSUMED_RECIPIENT_SETTINGS_PANEL": {
        "question": "What does the sender-side panel look like?",
        "left_open_by": (
            "The specification marks this itself: 'Recipient settings panel in the PandaDoc "
            "editor (verification method + timing) [inferred from the documented "
            "verification_settings object]'."
        ),
        "options": {
            "two_independent_pickers": (
                "One picker for the method and one for the moment, per gate, because the "
                "specification describes them as two independent axes."
            ),
            "one_combined_picker": "Four options, each a method-and-moment pair.",
            "no_panel": ("Serve the object over the API and let the seller use the command line."),
        },
        "chosen": "two_independent_pickers",
        "rejected_because": (
            "One combined picker was rejected because the specification's own table gives "
            "the moment its own row and its own audience column, and a combined picker cannot "
            "offer 'passcode before open, knowledge-based before sign' as one choice without "
            "it becoming four methods with two names. No panel was rejected because the "
            "specification names the panel as a product surface, and a control a reviewer "
            "cannot see is a control nobody reviews."
        ),
        "cost_of_the_choice": (
            "Only the panel's contents are sourced or derived. Its layout, order and "
            "placement are not, so they follow the design system rather than a vendor "
            "screenshot, and the object being edited is the sourced part."
        ),
    },
}


def describe() -> list[dict[str, Any]]:
    """Every recorded decision, in a stable order."""

    return [{"id": key, **value} for key, value in DECISIONS.items()]


def describe_one(decision_id: str) -> dict[str, Any]:
    """One decision by id, or an empty mapping the HTTP layer turns into a 404."""

    found = DECISIONS.get(decision_id)
    if found is None:
        return {}
    return {"id": decision_id, **found}


def count() -> int:
    return len(DECISIONS)


#: The three the specification itself marked inferred. Split out because a reader asking
#: "what in this workflow is assumed?" should not have to read every entry to find out.
ASSUMPTION_IDS = (
    "ASSUMED_KBA_PUBLIC_RECORD_SOURCE",
    "ASSUMED_ID_VERIFICATION_PROVIDER",
    "ASSUMED_RECIPIENT_SETTINGS_PANEL",
)


def assumptions() -> list[dict[str, Any]]:
    """Only the entries that record an assumption rather than a choice."""

    return [describe_one(key) for key in ASSUMPTION_IDS]
