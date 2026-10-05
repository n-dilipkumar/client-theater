"""WF-098: every judgement call this workflow rests on, and the alternative rejected.

The specification fixes the numbers and the surfaces and says almost nothing about the
mechanism. It names the 1-to-365-day window, the two reminder offsets, the account time
zone, the three acceptance methods, the three "won't expire if" rules, void, archive and
the resend cost. It does not say who sweeps the expiry, who dispatches the reminder, what
the reminder body says, or how a quote is held so a buyer action is time-checked.

Those gaps are decisions. They are collected here, each with the alternative this build
rejected and what would have gone wrong, so a reviewer reads the list instead of
reconstructing it from a diff. Every entry names the evidence it was taken from, and where
the specification says nothing, that is stated rather than papered over.

Two of these were put to Jev before any code was written, and the audit ids are recorded
against the entries they settled.
"""

from __future__ import annotations

from typing import Any

TICKET = "WF-098"

#: The two architecture questions settled with Jev before implementation.
#:
#: Recorded here rather than only in the audit log so a reader of the inferences register
#: sees that the mechanism choices were gated rather than picked.
JEV_DESIGN_AUDITS = {
    "domain_package_placement": "jev-20261004T225135-29568-95043",
    "job_driver": "jev-20261004T225135-29568-95339",
}


def _decision(
    decision_id: str,
    question: str,
    *,
    evidence: str,
    chosen: str,
    rejected: str,
    consequence: str,
    unsourced: bool = False,
) -> dict[str, Any]:
    return {
        "id": decision_id,
        "question": question,
        "evidence": evidence,
        "chosen": chosen,
        "rejected": rejected,
        "consequence": consequence,
        "unsourced": unsourced,
    }


#: Every decision, in the order a reader should meet them.
DECISIONS: list[dict[str, Any]] = [
    _decision(
        "domain-package-placement",
        "Where does WF-098's domain logic live, given the quoting-proposals package "
        "already exists?",
        evidence=(
            "backend/dsr/quoting_proposals/ exists on main, created by WF-093, and holds "
            "vocabulary.py, rules.py, inferences.py and engine.py for this exact domain. "
            "AGENTS.md and docs/FEATURE-CONTRACT.md both say one workflow ships as a "
            "plugin under a domain package, and the task brief says an existing package "
            "for the domain is to be extended rather than duplicated."
        ),
        chosen=(
            "Four new modules in backend/dsr/quoting_proposals/: "
            "quote_expiry_vocabulary.py, quote_expiry_rules.py, "
            "quote_expiry_inferences.py and quote_expiry_engine.py. The package's existing "
            "five files are not touched, including its __init__.py."
        ),
        rejected=(
            "A new backend/dsr/quote_expiry/ package, which would split one domain across "
            "two packages and leave two modules both called rules.py holding two sets of "
            "quote nouns. Also rejected: adding the four modules to the package's "
            "__init__.py, which is on main and is the first line a parallel branch working "
            "this domain would touch, so it converts an additive diff into a one-line "
            "context conflict."
        ),
        consequence=(
            "One domain, one package, four additive files. Python imports a submodule on "
            "demand, so the modules are reachable as "
            "dsr.quoting_proposals.quote_expiry_rules without the package __init__ "
            "knowing about them. A branch working this domain concurrently cannot conflict "
            "with this one. Put to Jev as audit jev-20261004T225135-29568-95043, which "
            "selected it at confidence 1.00 with a margin of 1.00 over the runner-up."
        ),
    ),
    _decision(
        "job-driver",
        "What drives the expiry check and the reminder dispatch, given the research calls "
        "them background jobs?",
        evidence=(
            "The automations section says: 'Scheduled reminder dispatch (time-relative to "
            "send and to expiry, in the account time zone).' and 'Expiry check is a "
            "background job: If a buyer hasn't accepted or signed a quote by the "
            "expiration date, it'll expire.' This product runs no worker and no scheduler, "
            "and every write's audit row must name a route the app actually serves."
        ),
        chosen=(
            "Two explicit POST routes, one for the expiry check and one for the reminder "
            "dispatch, both named in their own audit rows. The rules are pure functions of "
            "the stored send instant, the expiration date, the account time zone and the "
            "reminder ledger. Time moves because a caller asks."
        ),
        rejected=(
            "A background thread started at import time. It matches the research's word "
            "'background job' most literally, and it needs a clock no test can move, opens "
            "no request to attribute its writes to, and writes audit rows naming no route, "
            "which is the defect the feature contract names by name. Also rejected: "
            "deriving the state on every read, which reports a state the store does not "
            "hold and cannot write the 'Quote expired' activity a GET must not produce."
        ),
        consequence=(
            "Nothing expires until somebody calls the expiry check, and no reminder goes "
            "out until somebody calls the dispatch. A quote nobody checks is still readable "
            "past its deadline and the page shows it as due, so the gap is visible rather "
            "than silent. Every boundary in this workflow is reachable in a test because "
            "the clock is a parameter. Put to Jev as audit "
            "jev-20261004T225135-29568-95339, which selected it at confidence 1.00 with a "
            "margin of 1.00 over the runner-up."
        ),
        unsourced=True,
    ),
    _decision(
        "expiry-closes-acceptance-only",
        "What exactly does a quote's Expiration state take away?",
        evidence=(
            "The data flow says the buyer 'loses the ability to accept' and that 'expired "
            "quotes can still be downloaded, cloned, voided or archived'. The Void and "
            "Archive entries give the other two outcomes separately: Void 'will deactivate' "
            "the link URL, Archive 'unpublishes' and 'prevents buyers from accessing' the "
            "quote."
        ),
        chosen=(
            "Expired refuses the acceptance route and nothing else. Download, clone, void "
            "and archive all still work on an expired quote. Void sets the link inactive. "
            "Archive sets unpublished, hidden-from-index and no-buyer-access, as three "
            "separate fields."
        ),
        rejected=(
            "One inactive flag covering expired, voided and archived. It would answer "
            "identically to a client asking whether it may list the quote, fetch it by id "
            "and open its link, and the research gives three different answers to those "
            "three questions. It would also lose which of the three a caller asked for, "
            "which is the fact a seller needs when they come to undo it."
        ),
        consequence=(
            "Eight stored states rather than five, and three distinct field sets for void "
            "and archive. The page can say which consequence a quote carries because it is "
            "stored rather than inferred."
        ),
    ),
    _decision(
        "survival-is-three-actions-and-not-five",
        "Which buyer actions save a quote from its deadline?",
        evidence=(
            "'If a quote is accepted or signed before the expiration date, but hasn't been "
            "countersigned or paid, the quote won't expire.' and the data flow's 'accepted/"
            "e-signed/marked-signed by the expiration date'. The automations section adds "
            'that the three acceptance methods each have their own "won\'t expire if" rule, '
            "and does not enumerate them."
        ),
        chosen=(
            "Exactly three actions count: accepted, e_signed and marked_signed. Each must "
            "have happened at or before the deadline. Countersigned and paid are accepted "
            "as recorded values and deliberately do not save the quote."
        ),
        rejected=(
            "Treating countersigned and paid as surviving too, on the reasoning that a "
            "countersigned quote is further along than an accepted one. That reading "
            "contradicts the one hard sentence the research states about survival, and it "
            "would expire a quote the seller believes is finished."
        ),
        consequence=(
            "A quote countersigned on time and never accepted still expires, and a test "
            "asserts it. The acceptance method is stored, because the research says the "
            "three methods have separate rules, but all three reduce to the same predicate "
            "here: the buyer acted before the deadline. The per-method rule is recorded as "
            "unsourced, because the research names that the rules differ without stating "
            "them."
        ),
        unsourced=True,
    ),
    _decision(
        "acceptance-method-is-stored-not-branched",
        "The research says the three acceptance methods have separate expiry semantics. "
        "How is that modelled?",
        evidence=(
            "'Expiry semantics depend on acceptance method: e-signature/click-to-accept/"
            "print-and-sign each have their own \"won't expire if\" rule.' The sentence "
            "asserts that the rules differ and does not state any of them."
        ),
        chosen=(
            "The method is stored on every acceptance record and validated against the "
            "research's own three names, and the survival predicate is the same for all "
            "three. The method is refused when it is not one of the three, rather than "
            "defaulted, because a method this product does not know about is a method "
            "whose rule it cannot apply."
        ),
        rejected=(
            "Branching the survival predicate per method, inventing a different rule for "
            "each. Inventing a rule the research does not state would be worse than not "
            "implementing it: a quote would stop expiring for a reason no document "
            "supports, and nobody would be able to find the reason in a diff."
        ),
        consequence=(
            "The three methods are honoured as three named, validated, recorded values and "
            "the page shows which one a buyer used. If the per-method rules are ever "
            "sourced, they become one branch here and nothing else moves."
        ),
        unsourced=True,
    ),
    _decision(
        "resend-restarts-the-post-send-schedule",
        "Does a resend restart the 'days after sending quote' reminders?",
        evidence=(
            "'resending counts as a new send (consuming e-signature quota again)'. The "
            "data flow counts the reminder offsets from the send or publish timestamp. The "
            "research does not say what happens to those offsets on a second send."
        ),
        chosen=(
            "A resend sets a new send instant and the 'days after sending quote' rules count "
            "from it. A 'days before expiration date' rule is unaffected, because it counts "
            "from the expiration date and the resend does not move it."
        ),
        rejected=(
            "Counting the post-send offsets from the first send ever, which would mean a "
            "reminder fires on the original schedule and then never again, so a buyer who "
            "was reminded before the resend gets nothing after it. Also rejected: keeping "
            "both send instants and evaluating against each, which would send two reminders "
            "per rule and charge the seller for twice the nudges they configured."
        ),
        consequence=(
            "A resend is a fresh nudge schedule, which is what a seller who resends is "
            "asking for. The send count and the quota consumption are separate fields so "
            "the cost is visible, because the sentence is about consumption and a boolean "
            "cannot express it."
        ),
        unsourced=True,
    ),
    _decision(
        "settings-route-is-the-store",
        "Where does the reminder schedule live, given the research says no public write "
        "API exists?",
        evidence=(
            "'The reminder schedule is configured in settings (no documented public write "
            "API on the pages read).' The flow describes a settings screen with a reminder "
            "schedule, an add and a delete per rule, a send time and a preview."
        ),
        chosen=(
            "This product's own settings route is the store, and the gap is stated in the "
            "served vocabulary and on the page rather than left for a reader to infer from "
            "the absence of a vendor call."
        ),
        rejected=(
            "Writing the schedule through a fabricated HubSpot settings endpoint. A "
            "request to an endpoint this product cannot reach and cannot verify would "
            "either fail silently or, worse, be recorded as a successful write to a system "
            "that never received it."
        ),
        consequence=(
            "The schedule is durable and readable over this product's own API. Any "
            "integration with the real settings object has to be written against the real "
            "documentation, and this route is where its data lives."
        ),
        unsourced=True,
    ),
    _decision(
        "rules-are-rows-not-a-list-on-the-settings",
        "Where is a reminder rule stored?",
        evidence=(
            "'under *Reminder schedule* set the number of days and choose **Days after "
            "sending quote** or **Days before expiration date** to **+ Add reminder** / "
            "delete icon to manage several'. Each rule is separately created and separately "
            "deleted, and 'Multiple independent reminder rules' is in the extensibility list."
        ),
        chosen=(
            "One record per rule, so the delete names a rule and deleting one does not "
            "silently alter the others. A rule already sent for a quote is keyed on the "
            "rule id, so two rules with the same number of days are still two reminders."
        ),
        rejected=(
            "A list of rules inside the settings blob. It is one row instead of several "
            "and it makes the researched per-rule delete a string-indexed splice, which is "
            "the operation most likely to drop the wrong element when two agents edit the "
            "list at once."
        ),
        consequence=(
            "The schedule is filterable with find() on its indexed paths and the ledger is "
            "filterable on the rule and quote it belongs to. Storage is one row per rule, "
            "which the schema-flexible store absorbs without a migration."
        ),
    ),
    _decision(
        "each-rule-fires-once-per-quote",
        "Can one reminder rule send more than once for the same quote?",
        evidence=(
            "The flow describes a number of days, an offset kind and an add or delete per "
            "rule. It says nothing about repetition, frequency or a cap."
        ),
        chosen=(
            "Each rule fires once per quote. The ledger records the sent row, and the rule "
            "is skipped with a named reason on every later pass."
        ),
        rejected=(
            "Repeating a rule on an interval, which would need a cadence and an end "
            "condition the research does not state. Every such figure would be invented, "
            "and a reminder that keeps arriving after a buyer said no is the failure a "
            "seller would notice first."
        ),
        consequence=(
            "A seller who wants a third nudge adds a third rule, which is the shape the "
            "researched settings screen has. A skip is a ledger row, so the fact that a "
            "rule did not fire twice is visible rather than merely asserted."
        ),
        unsourced=True,
    ),
    _decision(
        "timezone-applies-to-the-hour-not-the-instant",
        "How does the account time zone change a reminder's instant?",
        evidence=(
            "'set *Reminder send time* (in the account time zone)' and the automations note "
            "'Scheduled reminder dispatch (time-relative to send and to expiry, in the "
            "account time zone)'."
        ),
        chosen=(
            "The send time is stored as a wall clock reading as HH:MM and never as an "
            "instant. The due instant is computed by taking the local date and placing that "
            "wall clock on it in the account's zone. A zone this build cannot resolve "
            "falls back to UTC and the response says so."
        ),
        rejected=(
            "Storing the send time as an instant at the moment it was set. That freezes "
            "the reading at whatever UTC offset happened to be in force, so a 09:00 "
            "reminder arrives at 08:00 or 10:00 local for half the year and nobody can see "
            "why. A guessed offset is worse still: a deadline displayed as another instant "
            "is a deadline a buyer trusts on the wrong day."
        ),
        consequence=(
            "A 09:00 reminder in Asia/Kolkata is 03:30 UTC, and it stays 09:00 local "
            "across a daylight-saving change in the sending country. The response carries "
            "the local reading, the offset and a note saying when the zone was not resolved."
        ),
    ),
    _decision(
        "date-picker-instant-is-midnight-utc",
        "A date with no time of day is midnight in which zone?",
        evidence=(
            "'click the **date picker** to set a specific date'. The research gives no time "
            "of day for the picker and no zone for it."
        ),
        chosen=(
            "Midnight UTC. It is the only reading that is the same instant on every machine, "
            "so a date shown to a seller in one zone and stored on another machine means the "
            "same thing."
        ),
        rejected=(
            "Midnight in the seller's local zone, which would make the stored deadline "
            "depend on the machine that submitted it. A deadline whose instant depends on "
            "the submitting machine is a deadline two parties can disagree about."
        ),
        consequence=(
            "A quote expiring on 2026-11-30 expires at 00:00 UTC on that date. The response "
            "reports the instant, the plain date and the local reading in the account's "
            "zone, so a seller who means end-of-day locally can see the difference."
        ),
    ),
    _decision(
        "the-quote-record-is-this-workflows-own",
        "Does WF-098 write the authored quote record, or hold its own state beside it?",
        evidence=(
            "The issue states WF-086 provisions the quote record and its hs_expiration_date "
            "property, and WF-094 provisions the send and publish events. The task brief "
            "and AGENTS.md both say a team adding a field must need no coordination with "
            "anyone, which argues against two features writing one record."
        ),
        chosen=(
            "WF-098 owns its own tracked-quote record holding the expiration date, the "
            "label, the switch, the send and publish instants, the send count, the "
            "acceptance entries and the void and archive flags. It names the record it "
            "tracks in source_quote_ref when one exists, and works without one."
        ),
        rejected=(
            "Writing the expiration date back onto WF-086's quote record. That gives one "
            "collection two writers, and the field the research calls required at quote "
            "creation would then be set by whichever feature ran last. It also makes this "
            "workflow unable to be reviewed until WF-086 has merged."
        ),
        consequence=(
            "A tracked quote carries the reference to what it tracks and works standalone, "
            "so the demo is reviewable whether or not WF-086 and WF-094 have merged. The "
            "expiry question is answered from this workflow's own state."
        ),
    ),
    _decision(
        "no-email-sending-is-claimed",
        "Does this workflow send a reminder email?",
        evidence=(
            "The research names HubSpot transactional email and the PandaDoc auto-reminder "
            "endpoints as data sources and APIs touched. This product sends no HTTP and "
            "holds no transactional email credential."
        ),
        chosen=(
            "The dispatch records the decision, the instant it was due and the per-recipient "
            "rows, and the preview route composes the reminder text from stored values. "
            "Delivery is the integrator's, and the response says so in a delivery field on "
            "every dispatch."
        ),
        rejected=(
            "Claiming a message went out. A ledger row asserting an email was sent to a "
            "buyer, when this product has no way to send one, is the worst failure this "
            "workflow could ship: it is a false record in the system whose whole guarantee "
            "is that its records are true."
        ),
        consequence=(
            "The schedule, the offsets, the time zone, the per-recipient ledger and the "
            "preview are all observable. An integration consumes the ledger and sends the "
            "message, and the page says which of the two happened."
        ),
        unsourced=True,
    ),
]


def count() -> int:
    return len(DECISIONS)


def describe() -> list[dict[str, Any]]:
    """Every recorded decision, sorted by id so the page is stable."""

    return sorted((dict(entry) for entry in DECISIONS), key=lambda row: str(row["id"]))


def describe_one(decision_id: str) -> dict[str, Any] | None:
    return next((entry for entry in DECISIONS if entry["id"] == decision_id), None)


def register() -> dict[str, Any]:
    """The whole register, as served at ``GET /inferences``."""

    return {
        "ticket": TICKET,
        "spec": "docs/research/digital-sales-room-workflows/wf/WF-098.md",
        "issue": 130,
        "count": count(),
        "unsourced_count": sum(1 for entry in DECISIONS if entry.get("unsourced")),
        "jev_design_audits": dict(JEV_DESIGN_AUDITS),
        "decisions": describe(),
        "sources": [
            "https://knowledge.hubspot.com/quotes/set-up-quotes",
            "https://knowledge.hubspot.com/quotes/manage-quotes",
            "https://knowledge.hubspot.com/quotes/create-and-send-quotes",
            "https://developers.pandadoc.com/llms.txt",
            "https://developers.pandadoc.com/reference/handledocumentstatechanged.md",
        ],
    }
