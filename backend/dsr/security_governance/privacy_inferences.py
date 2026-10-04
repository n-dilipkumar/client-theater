"""Every judgement call WF-085 made, with the alternative it rejected.

The specification instructs an implementer directly, seven times over, in the notes its
own issue quotes: the retention mapping "the spec does not say", the vendor enforcement
date this repository "does not have", the DNT signal it "records as unsupported", what
happens to records "already written", the role check it names rather than defines, and
the certifications it calls "a marketing claim". Each of those is answered here with the
option taken, the option rejected, and what the rejection would have cost.

A derivation with no rejected alternative recorded is a guess wearing a derivation's
clothes, so every entry names at least two options and says which one was taken.

The HTTP layer serves this table at ``GET /api/wf-085/decisions`` so the record is
readable by whoever reviews the feature, rather than buried in a docstring.
"""

from __future__ import annotations

from typing import Any

from dsr.security_governance import residency as vocab

DECISIONS: dict[str, dict[str, Any]] = {
    "DERIVED_REGION_MOVE_RESTAMPS_EVERY_RECORD": {
        "question": "What happens to records already written when the deployment changes region?",
        "left_open_by": (
            'The specification asserts that residency "is a deployment-time choice that '
            'fixes the jurisdiction of both content and engagement data" and says nothing '
            "about records that were already written when it changes."
        ),
        "options": {
            "restamp_in_place": (
                "Rewrite the region key on every live record in the deployment and report "
                "the count moved, so one jurisdiction always holds the room's data."
            ),
            "refuse_once_records_exist": (
                "Treat the choice as a one-way door: refuse any change once a record exists."
            ),
            "split_jurisdiction": (
                "Leave existing records in the old region and stamp only new ones."
            ),
            "purge_and_restamp": ("Hard-delete every existing record and stamp only new ones."),
        },
        "chosen": "restamp_in_place",
        "rejected_because": (
            "Splitting the jurisdiction is the one option that breaks the promise the "
            'control exists to keep: it is exactly the "region-based data residency '
            'control" the evidence names, and two regions means the room holds data in '
            "two places at once. Refusing the change is safe but leaves no way to correct "
            "a wrong region, and this product has no way to prove a room holds nothing - "
            "a claim of emptiness is not evidence of emptiness. Purging destroys "
            "engagement history on a configuration change, which is not what anyone asked "
            "for when they moved a region. Re-stamping keeps one jurisdiction, is "
            "mechanically possible because every field is JSON, and every re-stamp is one "
            "audited write so the change is traceable. Put to Jev as audit "
            "jev-20261004T182818-16136-98999, which selected it at confidence 0.84 with a "
            "margin of 0.76 over the runner-up."
        ),
        "cost_of_the_choice": (
            "A region change writes to every record in the deployment, so it is O(records) "
            "audited writes rather than one. The response reports how many moved and which "
            "collections they were in, so the cost is visible rather than hidden behind a "
            "single boolean."
        ),
    },
    "DERIVED_RETENTION_CLASS_MAPPING": {
        "question": "Which record type in this repository carries which retention class?",
        "left_open_by": (
            "The specification gives three classes and three windows in one sentence: "
            'recordings "up to 30 days from the time of recording", with favourites and a '
            "random sample up to 9 months; heatmaps up to 9 months. The classes are the "
            "vendor's and name vendor record types this repository does not have."
        ),
        "options": {
            "by_analogy": (
                "Map each of this repository's engagement collections onto the class that "
                "describes what the row records."
            ),
            "one_class_for_all": (
                "Put every collection on the strictest 30-day window, which needs no "
                "analogy at all."
            ),
            "configurable_mapping": (
                "Store the mapping in the database so an operator can set it per deployment."
            ),
        },
        "chosen": "by_analogy",
        "rejected_because": (
            "One class for all would purge the persistent visitor rows at 30 days, and that "
            "empties the viewers list and the DSAR surface in the same move - so the two "
            "features the specification names as the personal-data surface would have "
            "nothing left to answer a request with. A configurable mapping in the database "
            "was rejected because a class is a rule about what a record is, and this product "
            "puts rules in code: the moment a mapping is a stored value, a record can be "
            "aged against a rule nobody reviewed. Mapping by what the row records is "
            "checkable: a view event is a viewing session and takes the recording class, "
            "and an identifiable visitor row takes the 9-month class with the sample."
        ),
        "cost_of_the_choice": (
            "The mapping is a judgement, and a team whose engagement data means something "
            "different has to change code. Two things make that visible rather than silent: "
            "the served policy names the collections behind each class, and any live "
            "collection the table does not cover is reported as unmapped rather than "
            "assumed covered."
        ),
    },
    "DERIVED_RETENTION_WINDOW_IS_A_CEILING": {
        "question": "May a deployment configure a window longer than the researched figure?",
        "left_open_by": (
            'The evidence says "up to 30 days" and "up to 9 months". It gives maxima and '
            "no configuration surface, so it does not say whether a deployment may promise "
            "a different window."
        ),
        "options": {
            "ceiling_only": "The researched figures are fixed and nothing is configurable.",
            "configurable_up_to_ceiling": (
                "A deployment may promise a shorter window and never a longer one."
            ),
            "configurable_both_ways": "A deployment may set any window it likes.",
        },
        "chosen": "configurable_up_to_ceiling",
        "rejected_because": (
            'The word in the evidence is "up to", which is a maximum and not a target, so '
            "treating it as a fixed value would ignore the only quantifier the research "
            "gives. But letting a deployment set a longer window would let it exceed a "
            "figure the research sourced, and a compliance control that can be widened from "
            "the settings screen is not a control. Clamping rather than refusing was "
            "rejected for a specific reason: a clamp leaves the operator believing the "
            "longer window was agreed while the room enforces the shorter one, so the "
            "refusal names the ceiling and the request instead."
        ),
        "cost_of_the_choice": (
            "A deployment that wants a shorter window has to configure all three classes to "
            "get one, and the served policy reports the configured window and the ceiling "
            "separately so the difference is always on the page."
        ),
    },
    "DERIVED_CONSENT_GATE_IS_A_REGION_LIST_NOT_A_DATE": {
        "question": "Does the gate enforce from a fixed date, or for a configured set of regions?",
        "left_open_by": (
            'The evidence gives a vendor date: "Starting October 31, 2025, Clarity begins '
            "enforcing consent signal requirements for page visits originating from the EEA, "
            'UK, and Switzerland". The issue asks the implementer to "decide whether the '
            'repository honours a fixed date or a configured region list".'
        ),
        "options": {
            "fixed_date": ("Enforce from the vendor's date onward, for everyone, forever."),
            "region_list": (
                "Enforce for the jurisdictions the evidence names, as a configured list."
            ),
            "no_enforcement": "Record the date and enforce nothing.",
        },
        "chosen": "region_list",
        "rejected_because": (
            "The date is a vendor's enforcement date for a vendor's product. Honouring it "
            "here would mean this room gating a buyer in Ohio because a European vendor "
            "changed its policy on a date, and the date has already passed, so the gate "
            "would be permanently on with no way to see what turned it on. Enforcing "
            "nothing would drop the one control the specification calls a hard requirement. "
            "The evidence's own scoping sentence is the rule: consent is required \"for all "
            "incoming requests from end users located within the EEA, UK, and Switzerland "
            'and for websites targeting the EEA, UK, and Switzerland". That is a list of '
            "places, and a list is something a deployment configures and a reviewer reads."
        ),
        "cost_of_the_choice": (
            "A deployment that sells into the EU and misconfigures the list gates nothing. "
            "Every consent response therefore names the jurisdictions the gate is enforcing "
            "and the jurisdiction it resolved, so the misconfiguration is visible on the "
            "page rather than discovered during an audit."
        ),
    },
    "DERIVED_CONSENT_GATE_ENFORCES_IN_LISTED_JURISDICTIONS_ONLY": {
        "question": "In a region the list does not cover, what does the gate do?",
        "left_open_by": (
            'The specification says the gate must fail closed: "deny => unique ID per page '
            'view, no cookies" rather than "merely degrading". It scopes enforcement to the '
            "EEA, the UK and Switzerland, so it does not say what a region outside that scope "
            "should answer."
        ),
        "options": {
            "deny_everywhere": "Deny in every region until somebody consents, everywhere.",
            "track_outside_scope": "Enforce in the listed regions and track silently elsewhere.",
            "report_not_required": (
                "Report not_required outside the listed regions, and never silently track."
            ),
        },
        "chosen": "report_not_required",
        "rejected_because": (
            "Denying everywhere would mean a room sold into the United States silently loses "
            "every buyer it had, because nobody built a consent banner for a jurisdiction it "
            "does not serve - a worse failure than the one the gate prevents. Tracking "
            "silently was rejected on the same word the specification uses: a gate that "
            'falls through is exactly "merely degrading". So the unenforced region gets a '
            "third named answer, not_required, reported beside the two outcomes. A client "
            "then never has to infer it from a missing field, and the response says which "
            "jurisdictions were consulted."
        ),
        "cost_of_the_choice": (
            "The page renders three states rather than two, and the state a deployment is "
            "actually in is a configuration choice, so the board's job is to make that "
            "choice legible rather than to hide it."
        ),
    },
    "DERIVED_CONSENT_GATE_FAILS_CLOSED_ON_EVERY_NON_GRANT": {
        "question": "What does the gate answer when the consent signal is missing or unreadable?",
        "left_open_by": (
            'The issue names this as the defect to avoid: "A gate that falls through to full '
            'tracking on an error is a defect." The evidence fixes the deny behaviour but not '
            "the error behaviour."
        ),
        "options": {
            "fail_closed": ("Only the exact grant word allows tracking; everything else denies."),
            "default_grant": "A missing signal means the visitor has not objected, so track.",
            "fail_to_error": (
                "Refuse the page view outright rather than serving it with no identity."
            ),
        },
        "chosen": "fail_closed",
        "rejected_because": (
            "Defaulting to grant is the defect the issue names by name. Refusing the page "
            "view would deny the buyer their content, which is a worse answer than serving "
            "it with no cookies - the evidence's own deny behaviour serves the page and only "
            "withholds the identity. Fail-closed is also a property of the vocabulary rather "
            "than of a branch: there is exactly one grant word, so a new signal cannot widen "
            "the gate by being added somewhere else, and an unreadable one cannot slip "
            "through a comparison."
        ),
        "cost_of_the_choice": (
            "A client that sends a consent string this vocabulary does not know gets no "
            "tracking and no error, which reads as a silent failure. The gate therefore "
            "returns the reason and the signals it discarded, so the client can see which "
            "value was not the grant word."
        ),
    },
    "DERIVED_DNT_IS_NOT_A_CONSENT_SIGNAL": {
        "question": "Does a Do Not Track header grant or deny anything?",
        "left_open_by": (
            'The evidence says "Clarity doesn\'t currently respond to browser DNT signals", '
            'and the issue says "Do not implement DNT as a consent signal".'
        ),
        "options": {
            "ignored": "DNT is not read at all.",
            "deny_on_dnt": "DNT set denies, DNT clear does nothing.",
            "grant_on_clear": "DNT clear counts as consent.",
        },
        "chosen": "ignored",
        "rejected_because": (
            "Implementing DNT as a signal would make this room respond to a header the "
            "researched vendor does not respond to, so the two systems would disagree about "
            "the same visitor - one tracking a DNT browser and one not - and the room would "
            "be the one making a claim the research does not support. Treating DNT clear as "
            "consent is worse: it would grant on the absence of an objection, which is the "
            "same error as default-grant wearing a different header. Ignoring it is also the "
            "only option that leaves DNT working where it is documented to work, as a "
            "browser preference."
        ),
        "cost_of_the_choice": (
            "A client that sends DNT and nothing else is denied, which could be misread as "
            "DNT having been honoured and failed. The gate therefore reports the discarded "
            "signal and the reason beside every deny, so the absence is visible."
        ),
    },
    "DERIVED_DSAR_RETAINS_THE_AUDIT_TRAIL_AS_RESIDUE": {
        "question": "What happens to the audit rows describing a record this erasure removed?",
        "left_open_by": (
            'The specification names per-subject deletion as "the hard part" and quotes the '
            "vendor's limitation: \"You need to delete the entire project to delete user's "
            'data." It says nothing about the audit trail, and this repository writes one '
            "row per change with before and after snapshots."
        ),
        "options": {
            "erase_everything_including_audit": (
                "Remove the records and every audit row that mentions them."
            ),
            "keep_the_audit_trail": (
                "Remove the records, keep the audit rows, and report them as residue."
            ),
            "keep_an_erasure_witness_only": (
                "Collapse each removed record's audit rows into one row that names no content."
            ),
        },
        "chosen": "keep_the_audit_trail",
        "rejected_because": (
            "Removing the audit rows would falsify the log, and completeness of that log is "
            "the guarantee this whole repository is built on - a DSAR that achieves erasure "
            "by making the audit trail unreliable has not satisfied anybody. Collapsing rows "
            "does the same damage more elegantly: an audit trail that rewrites itself is a "
            "trail nobody can trust. The trail keeps the rows, and the response counts them "
            "and names the reason, because an erasure judged complete when it silently left "
            "copies behind is the failure mode the specification is warning about. The row "
            "recording the erasure is itself the evidence the erasure happened."
        ),
        "cost_of_the_choice": (
            "A subject's address survives in the audit trail's before and after snapshots, "
            "so the erasure is not an absolute removal and the response must not claim it is. "
            "It reports residue with counts. The platform's audit retention is not "
            "configurable from a feature, because the audited wrapper is a shared file."
        ),
    },
    "DERIVED_ADMINISTRATOR_ROLE_IS_READ_NOT_INVENTED": {
        "question": "Which role may make a blocking change, and where is it read?",
        "left_open_by": (
            'The specification names the check without defining the role: "role separation, '
            'since blocking changes are admin-only ("you need to be an *administrator* for '
            'your project")". The issue says "The repository has its own role surface. Read '
            'it rather than inventing a role." It does not say where that read happens.'
        ),
        "options": {
            "supplied_by_the_caller": (
                "The domain rules take the administrator tier as an argument, and the feature "
                "module - the one place allowed to read dsr.permissions - supplies it."
            ),
            "read_inside_the_rules_module": "The rules module imports dsr.permissions itself.",
            "written_out_locally": "This workflow declares the tier in its own vocabulary.",
        },
        "chosen": "supplied_by_the_caller",
        "rejected_because": (
            "A local admin string would be a second role vocabulary in one product, and the "
            "next feature would pick a third. That is the collision the plugin host exists "
            "to end, and it is worse here than elsewhere because a role decides who may "
            "delete a buyer's records. Reading dsr.permissions inside the rules module is the "
            "obvious choice and it was rejected on a measured fact: the domain package "
            "dsr/security_governance depends on nothing inside dsr but the store and itself, "
            "and tests/test_wf073.py enforces that for every module in it, so the direct "
            "import turns a pull request red on a file this branch may not edit. The room "
            "owner was rejected for a different reason: ownership is a fact about one room, "
            "while a data-subject erasure is a deployment-wide operation, and binding an "
            "org-wide obligation to one room's owner would make the control disappear the "
            "moment a room changed hands. Supplying the tier keeps the one role vocabulary "
            "and adds a seam: a test can gate against a tier of its own choosing, which a "
            "module-level constant could not offer. Put to Jev as audit "
            "jev-20261004T193716-19404-36869, which selected it at confidence 1.00 with a "
            "margin of 1.00 over the runner-up."
        ),
        "cost_of_the_choice": (
            "The administrator tier is a required argument on the engine and on the gate, so "
            "a caller that forgets it gets a wiring refusal rather than a silently ungated "
            "change. That is the better of the two failures, and the refusal names the field."
        ),
    },
    "DERIVED_VENDOR_CERTIFICATIONS_ARE_UNVERIFIED_CLAIMS": {
        "question": "What does the room say about the vendor's certifications?",
        "left_open_by": (
            'The evidence quotes a vendor page claiming "SOC 2 Type II, ISO 27001, ISO '
            '27701, GDPR, and the EU AI Act", and the issue says the certifications are "a '
            'marketing claim about a vendor" and "Do not render a certification badge as if '
            'the repository held the certificate."'
        ),
        "options": {
            "no_surface_at_all": "Say nothing about certifications anywhere.",
            "list_as_unverified_claims": (
                "Keep the quoted claims with the subject and a verified flag."
            ),
            "render_as_this_rooms_status": "Show the certifications as this room's own.",
        },
        "chosen": "list_as_unverified_claims",
        "rejected_because": (
            "Saying nothing discards the research, and the next agent to read the issue would "
            "re-derive it. Rendering them as this room's status is the thing the issue forbids "
            "outright: a badge on a deal room that says ISO 27001 would be a claim this "
            "repository cannot make and cannot audit. So the claims are kept verbatim with the "
            "subject they are about and verified false, which preserves the evidence and "
            "makes it impossible for a page to present them as a fact."
        ),
        "cost_of_the_choice": (
            "There is no badge, no tick and no compliant flag, so a legal reviewer asking "
            "what this room holds has to read the record rather than read a page. That is the "
            "correct answer, and the page says the same thing in words."
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


#: Served with the decisions so a reader can see the retention classes without opening
#: the vocabulary route as well.
RETENTION_CLASSES: list[str] = list(vocab.RETENTION_CLASSES)
