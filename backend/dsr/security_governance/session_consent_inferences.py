"""Every judgement call WF-083 made, with the alternative it rejected.

The specification fixes the numbers and leaves the mechanism open, so this register is
where the gaps are named. Each entry states the question, the sentence that left it open,
at least two options, the option taken, and what the rejection would have cost.

A derivation with no rejected alternative recorded is a guess wearing a derivation's
clothes. Every entry here names the alternative, because the specification itself asks for
it: "Decide what the room masks by default and record it."

The HTTP layer serves this table at ``GET /api/wf-083/decisions`` so the record is readable
by whoever reviews the feature rather than buried in a docstring.
"""

from __future__ import annotations

from typing import Any

from dsr.security_governance import session_consent as vocab

DECISIONS: dict[str, dict[str, Any]] = {
    "DERIVED_DEFAULT_MASKING_IS_TOTAL_SUPPRESSION": {
        "question": "What does the room mask when the project has configured no masking mode?",
        "left_open_by": (
            "The evidence says \"By default, Clarity suppresses the client's entire "
            'content." and asks the implementer to "Decide what the room masks by default '
            'and record it."'
        ),
        "options": {
            "suppress_all": (
                "Mask every value in every frame, which is the documented default, so a "
                "project that configures nothing transmits nothing."
            ),
            "mask_obvious_fields": (
                "Mask a fixed list of sensitive keys such as password and ssn, and keep the rest."
            ),
            "store_nothing_by_default": (
                "Store no frame at all until a project configures a masking mode."
            ),
        },
        "chosen": "suppress_all",
        "rejected_because": (
            "The fixed list is the dangerous option: it treats an unmasked value as the "
            "normal case and puts the burden of knowing what is sensitive on the operator, "
            "and the failure is silent because a replay that looks complete is exactly what "
            "a leaked field looks like. Storing nothing until a mode is configured is "
            "safer still, but it makes a recording project useless by default, and the "
            "specification describes a working default rather than a disabled product. "
            "Total suppression is the behaviour the evidence documents, so a project that "
            "configures nothing behaves as documented and a project that wants content "
            "configures a narrower mode on purpose."
        ),
        "cost_of_the_choice": (
            "A default project can record the fact of a visit without its content, so a "
            "reviewer opening a default project sees masked frames. The page says the mode "
            "in force on every row rather than leaving the reviewer to infer it."
        ),
    },
    "DERIVED_DENIAL_ON_ONE_AXIS_ENDS_THE_SESSION": {
        "question": "Does a denial on one axis end the persistent session, or only the other axis's storage?",
        "left_open_by": (
            'The evidence gives two independent axes and one teardown sentence: "Clarity '
            "deletes any existing cookie for the website, ends the current session, and "
            'restarts tracking in no-consent mode." It does not say which axis triggers it.'
        ),
        "options": {
            "either_axis_tears_down": (
                "A denial on either axis ends the session, so the identity becomes one "
                "identifier per page view and no cookie persists it."
            ),
            "both_axes_tear_down": (
                "Only a denial on both axes ends the session, so one granted axis keeps a "
                "persistent identity."
            ),
            "per_axis_identity": (
                "Each axis keeps its own identity, so ad storage and analytics storage "
                "track separately."
            ),
        },
        "chosen": "either_axis_tears_down",
        "rejected_because": (
            "The teardown sentence names cookies and the session, not a per-axis resource, "
            "and a cookie cannot be scoped to one axis. So a room that grants analytics "
            "while denying ads, which the evidence calls an ordinary setting, must still "
            "not persist the visitor. Both axes would leave a persistent identifier after "
            "one axis was denied, which is precisely the outcome the deny is meant to "
            "prevent. Per-axis identity is the most literal reading of two independent "
            "axes, and it was rejected for that reason: independence governs what each axis "
            "may store, not whether the browser keeps a cookie. The axes stay independent "
            "in every other respect, and this derivation is what makes a recording require "
            "both axes rather than either."
        ),
        "cost_of_the_choice": (
            "A buyer who allows analytics but refuses ads is still anonymous in the "
            "recordings, because no cookie persists. The response states the identity kind "
            "and the reason, so an operator can see that the recording was withheld for "
            "identity reasons rather than for a missing frame."
        ),
    },
    "DERIVED_GUEST_LINK_DEFAULT_WINDOW": {
        "question": "How long does a guest share link live when the caller names no window?",
        "left_open_by": (
            'The evidence says "sharing (guest links expire, team links don\'t)" and gives '
            "no number for the expiry."
        ),
        "options": {
            "seven_days": "A seven-day window, which is long enough to share in a meeting and short enough to expire.",
            "thirty_days": (
                "A thirty-day window, matching the ordinary recording retention the "
                "evidence gives elsewhere."
            ),
            "refuse_without_a_window": (
                "Refuse a guest link the caller did not give a window for."
            ),
        },
        "chosen": "seven_days",
        "rejected_because": (
            "Borrowing the thirty-day recording window conflates two different clocks: one "
            "is how long a recording is kept, the other is how long a link that exposes it "
            "resolves. Refusing without a window is the safest option and was rejected "
            "because it makes the ordinary share action fail for a value the page can "
            "supply. Seven days is recorded here as an inference rather than a sourced "
            "number, and the page prints the window beside the link so nobody has to guess "
            "which number came from research and which did not."
        ),
        "cost_of_the_choice": (
            "A share made for a long diligence window expires before the deal closes. The "
            "link record carries the expiry and the kind, so a caller can request a "
            "different window per link."
        ),
    },
    "DERIVED_ENFORCEMENT_USES_THE_VENDOR_DATE_AS_ITS_OWN": {
        "question": "On what schedule does this workflow enforce the consent gate by region?",
        "left_open_by": (
            'The evidence dates a vendor requirement: "Starting October 31, 2025, Clarity '
            "begins enforcing consent signal requirements for page visits originating from "
            'the European Economic Area (EEA), United Kingdom (UK), and Switzerland (CH)." '
            "It is a deadline for a vendor, not a rule for this repository."
        ),
        "options": {
            "vendor_date_as_default": (
                "Enforce from the same date on the same three regions, and let a caller "
                "override the list."
            ),
            "always_enforce": (
                "Enforce in every region always, because a legal gate is never optional."
            ),
            "never_enforce_by_region": (
                "Enforce on every visit regardless of region, and record the region only as data."
            ),
        },
        "chosen": "vendor_date_as_default",
        "rejected_because": (
            "Always enforcing is defensible and would be simpler, but it discards the one "
            "piece of scoping the research actually provides and would label a US visitor's "
            "visit as a region the evidence never mentions. Ignoring the date entirely was "
            "rejected for the mirror reason: a room deployed before the date would report "
            "itself as breaking a rule that did not yet exist. The date is therefore a "
            "named default a caller can override, and the response reports whether "
            "enforcement is active today so the behaviour is never implicit."
        ),
        "cost_of_the_choice": (
            "The gate is inert outside three regions until an operator configures more. "
            "That is the documented behaviour, and the board names the regions in force "
            "rather than implying a global rule."
        ),
    },
    "DERIVED_BLOCKED_VISIT_STORES_NO_RECORDING_ROW": {
        "question": "What does a blocked visitor leave behind?",
        "left_open_by": (
            'The evidence says "No sessions from visitors on the list are recorded" and '
            "names a console message instead: \"Data from this session isn't being "
            'collected by Microsoft Clarity due to your configured project settings."'
        ),
        "options": {
            "no_row_at_all": (
                "Write no recording row, and report the block in the response and the "
                "audit log only."
            ),
            "blocked_row": (
                "Write a recording row with a blocked state so the exclusion is countable "
                "in the recordings list."
            ),
            "aggregate_count_only": ("Keep a counter of excluded visits with no per-visit row."),
        },
        "chosen": "no_row_at_all",
        "rejected_because": (
            "A blocked row is still a stored session, which is the thing the evidence "
            "refuses, and it would hold the visitor's address. The aggregate counter was "
            "rejected because a count that cannot be audited is a number nobody can defend "
            "when asked how many internal viewers were excluded. The audit row records the "
            "refusal and the matched range, so the exclusion is traceable without a stored "
            "session, and the response carries the vendor's console message so the "
            "integration can be verified the way the documentation describes."
        ),
        "cost_of_the_choice": (
            "A blocked visit does not appear in the recordings list, so the list alone "
            "cannot answer how many visits were excluded. The summary counts them from the "
            "audit log and says so."
        ),
    },
    "DERIVED_RANDOM_SAMPLE_RETENTION_IS_NOT_DRAWN": {
        "question": "The evidence extends retention to a random sample of recordings. Which ones are sampled?",
        "left_open_by": (
            'The evidence says "Favorite recordings and randomly selected sample of '
            'recordings are retained for up to 9 months" and does not say how the sample '
            "is drawn, how large it is, or when the draw happens."
        ),
        "options": {
            "favourite_only": (
                "Implement the favourite case and leave the random sample to the vendor, "
                "because the specification fixes nothing about the draw."
            ),
            "deterministic_sample": (
                "Sample on a hash of the recording id, so the sample is reproducible."
            ),
            "every_tenth_recording": "Keep one recording in ten as the sample.",
        },
        "chosen": "favourite_only",
        "rejected_because": (
            "Any draw this product invents would be presented as the vendor's retention "
            "behaviour, and an invented rule that quietly extends how long a buyer's session "
            "is kept is the worst possible direction for an error. The favourite case is "
            "implemented because the specification states it directly and it is a flag the "
            "owner sets. The sample is left out, and the response says it was left out."
        ),
        "cost_of_the_choice": (
            "A project that relies on the vendor's sample retention gets the 30-day window "
            "here, which is shorter than the vendor's. The board names the shorter window "
            "so an operator is not surprised by it."
        ),
    },
    "DERIVED_TEAM_LINK_HAS_NO_EXPIRY_FIELD": {
        "question": "Does a team link carry an expiry at all?",
        "left_open_by": 'The evidence says "team links don\'t" expire and does not say what happens to the field.',
        "options": {
            "null_expiry": ("Store a null expiry and refuse to accept a window for a team link."),
            "store_but_ignore": "Store the window the caller sent and never enforce it.",
            "treat_as_guest": "Treat a team link as a guest link with a long window.",
        },
        "chosen": "null_expiry",
        "rejected_because": (
            "Storing a window and ignoring it produces a record that says one thing and "
            "behaves like another, which is how an operator ends up believing a team link "
            "expires. Treating it as a guest link contradicts the evidence outright. A null "
            "expiry is the honest shape, and a window supplied for a team link is refused "
            "rather than discarded silently."
        ),
        "cost_of_the_choice": (
            "A team link can never be time-boxed by this workflow. That is the documented "
            "behaviour, and the page says it in words beside the control."
        ),
    },
    "DERIVED_ADMINISTRATOR_ROLE_IS_READ_NOT_INVENTED": {
        "question": "Which role is the administrator the blocklist action requires?",
        "left_open_by": (
            'The evidence names the tier and does not define it: "To set up IP exclusion, '
            'you need to be an *administrator* for your project." This product already has '
            "a role surface with its own administrator tier."
        ),
        "options": {
            "read_the_products_tier": (
                "Read the administrator tier from the repository's role surface and hand it "
                "to the engine, so the gate compares against the tier this product uses."
            ),
            "invent_an_admin_role": (
                "Declare an 'admin' role inside this workflow and compare against that."
            ),
            "no_role_check": "Accept the write from anyone and record who asked.",
        },
        "chosen": "read_the_products_tier",
        "rejected_because": (
            "Declaring a second administrator role would give the product two answers to "
            "the same question, and a reviewer would have to decide which one a permission "
            "check elsewhere was using. Accepting the write from anyone is the failure the "
            "evidence forbids outright. So the tier is read rather than invented, the "
            "workflow depends on nothing inside dsr but the store and itself, and the "
            "vendor's word 'admin' is reported next to the product's tier so the "
            "correspondence is visible on the page rather than assumed."
        ),
        "cost_of_the_choice": (
            "The HTTP layer must accept a role parameter rather than authenticate a caller, "
            "because the specification rules out the authentication path this product would "
            "otherwise use. The page states the limitation and asks for a role."
        ),
    },
    "DERIVED_NO_SSO_PATH": {
        "question": "Does this workflow offer an authentication path?",
        "left_open_by": (
            "The evidence rules one out in its own words: \"Microsoft Clarity doesn't "
            "support authentication via your company's AAD instance.\" Membership is "
            '"email-invite based, Admin vs Team member roles".'
        ),
        "options": {
            "no_sso": (
                "Ship no authentication path, and report the membership model on every "
                "response that names a role."
            ),
            "add_sso_anyway": "Add an SSO path because the product has one elsewhere.",
            "model_only": "Leave authentication undefined rather than stating it is absent.",
        },
        "chosen": "no_sso",
        "rejected_because": (
            "The product does have roles elsewhere, so reusing them for this workflow is "
            "the tempting option, and it was rejected on evidence rather than on taste: the "
            "specification states the vendor does not support it, so a page offering SSO for "
            "this control would promise something the researched system cannot deliver. "
            "Leaving it undefined was rejected because an unstated absence reads as an "
            "oversight. Every response that reports a role carries "
            ":data:`~dsr.security_governance.session_consent.AUTHENTICATION`, so the "
            "membership model is stated rather than implied."
        ),
        "cost_of_the_choice": (
            "The admin check on IP blocking reads a role from the product's own role "
            "surface rather than authenticating anyone. The page therefore asks for a role "
            "and the limitation is stated on it."
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


#: Served with the decisions so a reader can see the masking default and the two retention
#: windows without opening the vocabulary route as well.
MASKING_DEFAULT = vocab.DEFAULT_MASKING_MODE
RETENTION_DAYS = {
    "ordinary": vocab.ORDINARY_RETENTION_DAYS,
    "favourite": vocab.FAVOURITE_RETENTION_DAYS,
}
