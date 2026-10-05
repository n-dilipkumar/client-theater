"""The words WF-096 speaks, in one place.

Every collection name, every status, every refusal reason code and every researched sentence
this workflow enforces against is declared here rather than written as a literal at its use
site. The reason is the one this repository keeps relearning: a literal at the use site is a
value the vocabulary endpoint cannot report, so a reviewer cannot tell a deliberate decision
from a typo.

``vocabulary()`` is served over HTTP at ``GET /api/wf-096/vocabulary`` so the page reads its
words from the server instead of hard-coding them. A change to a rule then cannot leave the
page calling a state the API does not serve.

Where the words come from
-------------------------

The research is ``docs/research/raw/quoting-proposals.md`` section 11, "Accept a quote without
a signature and take payment in the quote", quoted in full in issue 178. Every constant below
that the research fixes carries the sentence it came from, so a reader can tell which parts
are a specification and which are a derivation. The derived parts are named ``DERIVED_*`` in
:mod:`dsr.quote_payment.inferences` rather than being smuggled in here as if they were sourced.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: The quote itself. WF-086 provisions it. This workflow reads it as data and patches exactly
#: five properties onto it — the four the data flow names on publish plus the two acceptance
#: fields — and never touches a line item, an amount or a total. The reason to patch it at all
#: is that the researched data flow says the accept action "writes ``hs_clickwrap_accepted_by``
#: and flips ``hs_status`` to ``ACCEPTED``", and a downstream reader (WF-099, WF-094) looks for
#: those two properties on the quote rather than in this workflow's tables.
QUOTE_COLLECTION = "wf086_quote"

#: Quote line items, owned by WF-086. This workflow reads them to compute the amount due and
#: to decide which lines recur. It writes a line item only through its own demo route, which
#: exists so the flow is demonstrable before WF-086 is exercised from the UI.
LINE_ITEM_COLLECTION = "wf086_line_item"

#: One row per quote's payment provisioning: the publish-time payment configuration. This is
#: the researched publish step, and it is the row every other write looks up first.
PAYMENT_SETUP_COLLECTION = "wf096_payment_setup"

#: One row per clickwrap acceptance. This is the "Accept without signature" act itself: who
#: accepted, when, and the method. A quote has at most one.
ACCEPTANCE_COLLECTION = "wf096_acceptance"

#: One row per charge attempt, whether it was recorded or declined. A declined charge is a row
#: and not an exception, because the minimum-charge constraint is a payment-processor outcome
#: ("the total amount due must be more than $0.50"), not a malformed request.
CHARGE_COLLECTION = "wf096_charge"

#: One row per invoice. The first invoice is sent immediately on acceptance; each later invoice
#: is scheduled and sent ten days before its invoice date.
INVOICE_COLLECTION = "wf096_invoice"

#: One row per recurring line item's subscription. Billing frequency and billing start date are
#: per line item, so a subscription is per line item rather than per quote.
SUBSCRIPTION_COLLECTION = "wf096_subscription"

#: Buyer-supplied tax identifiers. The research caps them at three.
TAX_ID_COLLECTION = "wf096_tax_id"

#: The human-readable activity log: publish, accepted, payment recorded, tax ID added, void.
ACTIVITY_COLLECTION = "wf096_activity"

#: Every collection this workflow writes. Read by the summary so a reviewer can see the blast
#: radius of the feature in one list.
OWNED_COLLECTIONS = (
    PAYMENT_SETUP_COLLECTION,
    ACCEPTANCE_COLLECTION,
    CHARGE_COLLECTION,
    INVOICE_COLLECTION,
    SUBSCRIPTION_COLLECTION,
    TAX_ID_COLLECTION,
    ACTIVITY_COLLECTION,
)

#: Collections this workflow reads and does not own. Named here so the boundary is one list a
#: reviewer can check rather than a scattering of literals.
READ_ONLY_COLLECTIONS = (QUOTE_COLLECTION, LINE_ITEM_COLLECTION)

# --------------------------------------------------------------------------- #
# The store's own envelope
# --------------------------------------------------------------------------- #

#: The only fixed record vocabulary. Every other field is a JSON value inside ``data``.
ENVELOPE_FIELDS = (
    "id",
    "collection",
    "room_id",
    "revision",
    "created_at",
    "updated_at",
    "deleted_at",
)

# --------------------------------------------------------------------------- #
# Acceptance methods
# --------------------------------------------------------------------------- #

#: "accept a quote online with an e-signature". WF-095 owns this path; this workflow recognises
#: it so it can refuse it with a reason that names where it belongs.
ACCEPTANCE_ESIGNATURE = "esignature"

#: "the quote can be published to allow buyers to accept without a signature (clickwrap)".
#: This is the method this workflow exists for.
ACCEPTANCE_CLICKWRAP = "clickwrap"

#: "Print and sign". Valid on a quote without online payments, and never valid with them.
ACCEPTANCE_PRINT_AND_SIGN = "print_and_sign"

ACCEPTANCE_METHODS = (ACCEPTANCE_ESIGNATURE, ACCEPTANCE_CLICKWRAP, ACCEPTANCE_PRINT_AND_SIGN)

ACCEPTANCE_METHOD_LABELS = {
    ACCEPTANCE_ESIGNATURE: "E-signature",
    ACCEPTANCE_CLICKWRAP: "Accept without signature",
    ACCEPTANCE_PRINT_AND_SIGN: "Print and sign",
}

#: The two methods the research permits when the quote offers online payments:
#: "online payments require an acceptance method of *E-signature* or *Accept without
#: signature*. *Print and sign* isn't a valid acceptance method with online payments."
ONLINE_PAYMENT_ACCEPTANCE_METHODS = (ACCEPTANCE_ESIGNATURE, ACCEPTANCE_CLICKWRAP)

#: Synonyms a caller may send, normalised to the three values above. The research writes the
#: three snake_case tokens in the data flow (``clickwrap``, ``esignature``, ``print_and_sign``)
#: and the labels in the UI, so both spellings are accepted.
ACCEPTANCE_METHOD_ALIASES = {
    "e_signature": ACCEPTANCE_ESIGNATURE,
    "esign": ACCEPTANCE_ESIGNATURE,
    "electronic_signature": ACCEPTANCE_ESIGNATURE,
    "accept_without_signature": ACCEPTANCE_CLICKWRAP,
    "accept_without_a_signature": ACCEPTANCE_CLICKWRAP,
    "click_wrap": ACCEPTANCE_CLICKWRAP,
    "no_signature": ACCEPTANCE_CLICKWRAP,
    "print": ACCEPTANCE_PRINT_AND_SIGN,
    "print_sign": ACCEPTANCE_PRINT_AND_SIGN,
    "print_and_signed": ACCEPTANCE_PRINT_AND_SIGN,
}

#: What ``hs_clickwrap_accepted_by`` holds when the seller chose *Do not specify* under
#: *Request acceptance from*. The research: "a contact doesn't need to be added to the quote",
#: so an anonymous acceptance is legitimate and must still name the party honestly.
CLICKWRAP_ANONYMOUS_BUYER = "Buyer (no contact specified)"

#: The three choices under *Request acceptance from*: "choose *Do not specify*, a specific
#: contact, or added contacts".
ACCEPTANCE_RECIPIENT_MODES = ("do_not_specify", "specific_contact", "added_contacts")

# --------------------------------------------------------------------------- #
# Quote properties this workflow writes
# --------------------------------------------------------------------------- #

#: The published quote's status, flipped to ``ACCEPTED`` by the accept action. The data flow
#: names this exact write.
FIELD_HS_STATUS = "hs_status"
QUOTE_STATUS_ACCEPTED = "ACCEPTED"
QUOTE_STATUS_VOID = "VOID"

#: Who accepted without a signature. The data flow names this exact property.
FIELD_HS_CLICKWRAP_ACCEPTED_BY = "hs_clickwrap_accepted_by"

#: Set to ``PENDING`` on publish. The data flow names this exact write, and this workflow does
#: not move it afterwards because no researched step moves it and no money has actually settled.
FIELD_HS_PAYMENT_STATUS = "hs_payment_status"
PAYMENT_STATUS_PENDING = "PENDING"

#: The billing and payment switches the publish step turns on. Both are in the data flow's
#: publish tuple.
FIELD_HS_BILLING_ENABLED = "hs_billing_enabled"
FIELD_HS_PAYMENT_ENABLED = "hs_payment_enabled"

#: HubSpot payments or a connected Stripe account. The research: "HubSpot payments vs Stripe
#: are swappable per account, and ``hs_payment_type`` is set automatically."
FIELD_HS_PAYMENT_TYPE = "hs_payment_type"

#: The payment methods the buyer may use at checkout.
FIELD_HS_ALLOWED_PAYMENT_METHODS = "hs_allowed_payment_methods"

#: Whether checkout collects a billing or shipping address, and how the balance is collected.
FIELD_HS_COLLECT_BILLING_ADDRESS = "hs_collect_billing_address"
FIELD_HS_COLLECT_SHIPPING_ADDRESS = "hs_collect_shipping_address"
FIELD_HS_COLLECTION_PROCESS = "hs_collection_process"
FIELD_HS_NET_PAYMENT_TERMS = "hs_net_payment_terms"

#: Every quote property this workflow patches. A test asserts this list is exactly the set of
#: keys the engine writes onto ``wf086_quote``, so a later change cannot silently widen the
#: write surface to a quote's amounts.
QUOTE_PROPERTIES_WRITTEN = (
    FIELD_HS_STATUS,
    FIELD_HS_CLICKWRAP_ACCEPTED_BY,
    FIELD_HS_BILLING_ENABLED,
    FIELD_HS_PAYMENT_ENABLED,
    FIELD_HS_PAYMENT_TYPE,
    FIELD_HS_PAYMENT_STATUS,
    FIELD_HS_ALLOWED_PAYMENT_METHODS,
    FIELD_HS_COLLECT_BILLING_ADDRESS,
    FIELD_HS_COLLECT_SHIPPING_ADDRESS,
    FIELD_HS_COLLECTION_PROCESS,
    FIELD_HS_NET_PAYMENT_TERMS,
)

# --------------------------------------------------------------------------- #
# Payment methods
# --------------------------------------------------------------------------- #

#: The five methods the data flow lists for ``hs_allowed_payment_methods``.
PAYMENT_ACH = "ACH"
PAYMENT_CARD = "CREDIT_OR_DEBIT_CARD"
PAYMENT_SEPA = "SEPA"
PAYMENT_BACS = "BACS"
PAYMENT_PADS = "PADS"

PAYMENT_METHODS = (PAYMENT_ACH, PAYMENT_CARD, PAYMENT_SEPA, PAYMENT_BACS, PAYMENT_PADS)

PAYMENT_METHOD_LABELS = {
    PAYMENT_ACH: "ACH",
    PAYMENT_CARD: "Credit or debit card",
    PAYMENT_SEPA: "SEPA direct debit",
    PAYMENT_BACS: "BACS direct debit",
    PAYMENT_PADS: "PADS pre-authorized debit",
}

#: Spellings the page and the API accept for the five methods above.
PAYMENT_METHOD_ALIASES = {
    "CARD": PAYMENT_CARD,
    "CREDIT_CARD": PAYMENT_CARD,
    "DEBIT_CARD": PAYMENT_CARD,
    "CREDIT_OR_DEBIT": PAYMENT_CARD,
    "CREDIT_OR_DEBIT_CARDS": PAYMENT_CARD,
    "BANK_TRANSFER": PAYMENT_ACH,
    "DIRECT_DEBIT": PAYMENT_ACH,
    "SEPA_DIRECT_DEBIT": PAYMENT_SEPA,
    "BACS_DIRECT_DEBIT": PAYMENT_BACS,
    "PRE_AUTHORIZED_DEBIT": PAYMENT_PADS,
}

#: What a checkout collects by default when the payload names no methods. One card method keeps
#: a wrong-but-valid default from quietly enabling five rails a seller never chose.
DEFAULT_PAYMENT_METHODS = (PAYMENT_CARD,)

# --------------------------------------------------------------------------- #
# Payment type
# --------------------------------------------------------------------------- #

#: "HubSpot payments" — the platform's own processing.
PAYMENT_TYPE_HUBSPOT = "HUBSPOT"

#: "a connected Stripe account" — the seller's own processing.
PAYMENT_TYPE_BYO_STRIPE = "BYO_STRIPE"

PAYMENT_TYPES = (PAYMENT_TYPE_HUBSPOT, PAYMENT_TYPE_BYO_STRIPE)

PAYMENT_TYPE_LABELS = {
    PAYMENT_TYPE_HUBSPOT: "HubSpot payments",
    PAYMENT_TYPE_BYO_STRIPE: "Your own Stripe account",
}

#: The payload keys that tell this workflow the account has a connected Stripe account. The
#: research says the type "is set automatically", so the seller never chooses it; these keys
#: describe the account, and the derivation is recorded in
#: :data:`dsr.quote_payment.inferences.DERIVED_PAYMENT_TYPE_IS_DERIVED_NOT_CHOSEN`.
CONNECTED_STRIPE_KEYS = ("connected_stripe_account", "stripe_account_id", "byo_stripe_account")

# --------------------------------------------------------------------------- #
# Collection process and net terms
# --------------------------------------------------------------------------- #

#: The one ``hs_collection_process`` value the data flow names, "``AUTO_PAYMENTS``": charge the
#: stored method automatically.
COLLECTION_PROCESS_AUTO_PAYMENTS = "AUTO_PAYMENTS"
COLLECTION_PROCESSES = (COLLECTION_PROCESS_AUTO_PAYMENTS,)
COLLECTION_PROCESS_LABELS = {COLLECTION_PROCESS_AUTO_PAYMENTS: "Automatic payments"}

#: ``hs_net_payment_terms``. The research names the property and gives no vocabulary, so the
#: product's own calendar is declared here and the absence of a researched list is recorded in
#: :data:`dsr.quote_payment.inferences.DERIVED_NET_PAYMENT_TERMS_VOCABULARY`.
NET_PAYMENT_TERMS = ("NET_0", "NET_15", "NET_30", "NET_45", "NET_60")
DEFAULT_NET_PAYMENT_TERMS = "NET_30"

# --------------------------------------------------------------------------- #
# Billing frequency
# --------------------------------------------------------------------------- #

#: A line billed once. The default for a line that names no frequency.
BILLING_ONE_TIME = "one_time"
BILLING_MONTHLY = "monthly"
BILLING_QUARTERLY = "quarterly"
BILLING_SEMI_ANNUAL = "semi_annual"
BILLING_ANNUAL = "annual"

BILLING_FREQUENCIES = (
    BILLING_ONE_TIME,
    BILLING_MONTHLY,
    BILLING_QUARTERLY,
    BILLING_SEMI_ANNUAL,
    BILLING_ANNUAL,
)

BILLING_FREQUENCY_LABELS = {
    BILLING_ONE_TIME: "One time",
    BILLING_MONTHLY: "Monthly",
    BILLING_QUARTERLY: "Quarterly",
    BILLING_SEMI_ANNUAL: "Every six months",
    BILLING_ANNUAL: "Annual",
}

#: How many months apart each recurring frequency is. Used to lay out the invoice schedule.
BILLING_PERIOD_MONTHS = {
    BILLING_MONTHLY: 1,
    BILLING_QUARTERLY: 3,
    BILLING_SEMI_ANNUAL: 6,
    BILLING_ANNUAL: 12,
}

BILLING_FREQUENCY_ALIASES = {
    "once": BILLING_ONE_TIME,
    "one-time": BILLING_ONE_TIME,
    "one_off": BILLING_ONE_TIME,
    "single": BILLING_ONE_TIME,
    "month": BILLING_MONTHLY,
    "yearly": BILLING_ANNUAL,
    "annually": BILLING_ANNUAL,
    "year": BILLING_ANNUAL,
    "quarter": BILLING_QUARTERLY,
    "semi-annual": BILLING_SEMI_ANNUAL,
    "semiannual": BILLING_SEMI_ANNUAL,
    "semi_annually": BILLING_SEMI_ANNUAL,
    "half_yearly": BILLING_SEMI_ANNUAL,
}

#: The three future invoices this workflow schedules per recurring line. The research names no
#: horizon, so the number is a derivation, not a sourced figure: three periods is enough to show
#: the schedule's shape without writing a row per month for a five-year subscription.
SUBSEQUENT_INVOICE_PERIODS = 3

# --------------------------------------------------------------------------- #
# Effective date
# --------------------------------------------------------------------------- #

#: "Optionally set the effective date (**On agreement** / **Custom Date** / **Delayed start
#: (days)** / **Delayed start (months)**)."
EFFECTIVE_ON_AGREEMENT = "on_agreement"
EFFECTIVE_CUSTOM_DATE = "custom_date"
EFFECTIVE_DELAYED_DAYS = "delayed_days"
EFFECTIVE_DELAYED_MONTHS = "delayed_months"

EFFECTIVE_DATE_MODES = (
    EFFECTIVE_ON_AGREEMENT,
    EFFECTIVE_CUSTOM_DATE,
    EFFECTIVE_DELAYED_DAYS,
    EFFECTIVE_DELAYED_MONTHS,
)

EFFECTIVE_DATE_MODE_LABELS = {
    EFFECTIVE_ON_AGREEMENT: "On agreement",
    EFFECTIVE_CUSTOM_DATE: "Custom date",
    EFFECTIVE_DELAYED_DAYS: "Delayed start (days)",
    EFFECTIVE_DELAYED_MONTHS: "Delayed start (months)",
}

DEFAULT_EFFECTIVE_DATE_MODE = EFFECTIVE_ON_AGREEMENT

# --------------------------------------------------------------------------- #
# Charge outcomes
# --------------------------------------------------------------------------- #

#: The processor took the charge. Not "paid": no money is moved by this build, so the word is
#: the outcome of recording the charge intent, and the quote's ``hs_payment_status`` stays
#: ``PENDING`` until a real processor settles it.
OUTCOME_RECORDED = "recorded"

#: The processor refused the charge. The researched refusal is the minimum-charge constraint.
OUTCOME_DECLINED = "declined"

CHARGE_OUTCOMES = (OUTCOME_RECORDED, OUTCOME_DECLINED)

CHARGE_OUTCOME_LABELS = {
    OUTCOME_RECORDED: "Charge recorded",
    OUTCOME_DECLINED: "Charge declined",
}

#: The buyer may store the card for later charges: "a 'store the buyer's payment method for
#: future charges' checkbox exists".
FIELD_STORE_PAYMENT_METHOD = "store_payment_method"

# --------------------------------------------------------------------------- #
# Invoices, subscriptions and the minimum charge
# --------------------------------------------------------------------------- #

#: The first invoice: "generated and sent to the buyer immediately after quote acceptance,
#: regardless of its scheduled invoice date or due date."
INVOICE_FIRST = "first"
INVOICE_SCHEDULED = "scheduled"

INVOICE_KINDS = (INVOICE_FIRST, INVOICE_SCHEDULED)

INVOICE_STATUS_SENT = "sent"
INVOICE_STATUS_SCHEDULED = "scheduled"

INVOICE_STATUSES = (INVOICE_STATUS_SENT, INVOICE_STATUS_SCHEDULED)

#: "Subsequent invoices are generated and sent according to the contract's billing schedule,
#: 10 days before their invoice dates."
INVOICE_LEAD_DAYS = 10

#: A subscription is created for a recurring line, and is active until the term ends. The
#: research names no terminal state, so only the two an invoice schedule needs are declared.
SUBSCRIPTION_ACTIVE = "active"
SUBSCRIPTION_STATUSES = (SUBSCRIPTION_ACTIVE,)

#: "when using HubSpot payments or Stripe as your payment processing option, the total amount
#: due must be more than $0.50, or the equivalent minimum of the settlement currency." The
#: comparison is strict: exactly 0.50 is refused.
MINIMUM_CHARGE_USD = 0.50

#: Per-currency minimums the research states the shape of and no value for, except USD. A
#: currency not listed falls back to the USD figure, and the fallback is recorded in
#: :data:`dsr.quote_payment.inferences.DERIVED_MINIMUM_CHARGE_IS_USD_ANCHORED`.
MINIMUM_CHARGE_BY_CURRENCY = {"USD": MINIMUM_CHARGE_USD}

DEFAULT_CURRENCY = "USD"

#: The currency field WF-086 writes on a quote, read here rather than re-derived.
QUOTE_CURRENCY_FIELDS = ("currency", "currency_label", "transactioncurrency")

# --------------------------------------------------------------------------- #
# Tax
# --------------------------------------------------------------------------- #

#: "Buyers can add up to three tax IDs to the quote."
TAX_ID_LIMIT = 3

#: Whether automated sales tax is recalculated at quote creation. The research says it "will be
#: calculated when the quote is created"; this workflow carries the switch and the per-line tax
#: rate, and computes the tax from the line rate, because it does not own the quote creation.
FIELD_AUTOMATED_SALES_TAX = "automated_sales_tax"

# --------------------------------------------------------------------------- #
# Money
# --------------------------------------------------------------------------- #

#: Every money figure is rounded half up to this many places, once per line and once per total,
#: so the printed lines sum to the printed total.
MONEY_PLACES = 2

DISCOUNT_PERCENTAGE = "percentage"
DISCOUNT_CURRENCY = "currency"
DISCOUNT_TYPES = (DISCOUNT_PERCENTAGE, DISCOUNT_CURRENCY)

# --------------------------------------------------------------------------- #
# Activity log
# --------------------------------------------------------------------------- #

#: The human-readable activity rows this workflow appends. The research names activities for
#: the e-signature path; these are this workflow's own, because publishing payments, accepting
#: without a signature and recording a charge are acts a seller reviews on the board.
ACTIVITY_PAYMENT_ENABLED = "payment_enabled"
ACTIVITY_QUOTE_ACCEPTED = "quote_accepted"
ACTIVITY_PAYMENT_RECORDED = "payment_recorded"
ACTIVITY_PAYMENT_DECLINED = "payment_declined"
ACTIVITY_TAX_ID_ADDED = "tax_id_added"
ACTIVITY_QUOTE_VOIDED = "quote_voided"

ACTIVITIES = (
    ACTIVITY_PAYMENT_ENABLED,
    ACTIVITY_QUOTE_ACCEPTED,
    ACTIVITY_PAYMENT_RECORDED,
    ACTIVITY_PAYMENT_DECLINED,
    ACTIVITY_TAX_ID_ADDED,
    ACTIVITY_QUOTE_VOIDED,
)

ACTIVITY_LABELS = {
    ACTIVITY_PAYMENT_ENABLED: "Payment enabled",
    ACTIVITY_QUOTE_ACCEPTED: "Quote accepted",
    ACTIVITY_PAYMENT_RECORDED: "Payment recorded",
    ACTIVITY_PAYMENT_DECLINED: "Payment declined",
    ACTIVITY_TAX_ID_ADDED: "Tax ID added",
    ACTIVITY_QUOTE_VOIDED: "Quote voided",
}

# --------------------------------------------------------------------------- #
# Refusal reason codes
# --------------------------------------------------------------------------- #
#
# Every refusal this workflow can make carries one of these. A reason code is published rather
# than embedded in prose, because a caller and a test both need to branch on it and neither
# should pattern-match a sentence.

#: ``hs_acceptance_method`` is not one of the three the research names.
REASON_UNKNOWN_ACCEPTANCE_METHOD = "unknown_acceptance_method"

#: "Print and sign isn't a valid acceptance method with online payments."
REASON_PRINT_AND_SIGN_WITH_ONLINE_PAYMENTS = "print_and_sign_with_online_payments"

#: The accept route was asked to record an e-signature acceptance. That path is WF-095's.
REASON_E_SIGNATURE_BELONGS_TO_WF095 = "e_signature_acceptance_belongs_to_wf095"

#: The quote's own acceptance method is not *Accept without signature*, so this workflow's
#: clickwrap route will not record an acceptance against it.
REASON_ACCEPTANCE_METHOD_IS_NOT_CLICKWRAP = "acceptance_method_is_not_accept_without_signature"

#: A line item has a fractional quantity while billing or online payments are on. The
#: research: "if the quote offers online payments or the *Enable billing* switch is on, line
#: item quantities must be whole numbers."
REASON_FRACTIONAL_QUANTITY_WITH_BILLING = "fractional_quantity_with_billing"

#: "the total amount due must be more than $0.50". Recorded as a declined charge.
REASON_AMOUNT_DUE_BELOW_MINIMUM = "amount_due_below_minimum"

#: A payment method not in the researched five.
REASON_UNKNOWN_PAYMENT_METHOD = "unknown_payment_method"

#: A payment method that is real but not among this quote's allowed methods.
REASON_PAYMENT_METHOD_NOT_ALLOWED = "payment_method_not_allowed"

#: A publish with no allowed payment methods left after normalisation.
REASON_NO_PAYMENT_METHODS = "no_payment_methods"

#: A publish with no line items. There is no amount to charge.
REASON_QUOTE_IS_EMPTY = "quote_has_no_line_items"

#: An accept, charge or configuration change on a quote that was never published with payments.
REASON_QUOTE_NOT_PROVISIONED = "quote_is_not_published_with_payments"

#: A second accept on a quote that already has one.
REASON_QUOTE_ALREADY_ACCEPTED = "quote_already_accepted"

#: "Quotes can't be voided or deleted after they have been accepted."
REASON_IRREVERSIBLE_AFTER_ACCEPTANCE = "quote_is_irreversible_after_acceptance"

#: A second charge on a quote that already has one.
REASON_CHARGE_ALREADY_RECORDED = "charge_already_recorded"

#: A charge attempted before the quote was accepted.
REASON_PAYMENT_REQUIRES_ACCEPTANCE = "payment_requires_acceptance"

#: A fourth tax ID. "Buyers can add up to three tax IDs to the quote."
REASON_TAX_ID_LIMIT_REACHED = "tax_id_limit_reached"

#: An empty tax ID value.
REASON_TAX_ID_EMPTY = "tax_id_value_is_empty"

#: An effective-date mode or its argument that cannot be resolved to a date.
REASON_INVALID_EFFECTIVE_DATE = "invalid_effective_date"

#: A billing frequency not in the derived five.
REASON_UNKNOWN_BILLING_FREQUENCY = "unknown_billing_frequency"

#: A collection process not in the researched set.
REASON_UNKNOWN_COLLECTION_PROCESS = "unknown_collection_process"

#: A net payment term not in the product's own calendar.
REASON_UNKNOWN_NET_PAYMENT_TERMS = "unknown_net_payment_terms"

#: Every reason code, for the vocabulary endpoint and for a test that pins the set.
REASON_CODES = (
    REASON_UNKNOWN_ACCEPTANCE_METHOD,
    REASON_PRINT_AND_SIGN_WITH_ONLINE_PAYMENTS,
    REASON_E_SIGNATURE_BELONGS_TO_WF095,
    REASON_ACCEPTANCE_METHOD_IS_NOT_CLICKWRAP,
    REASON_FRACTIONAL_QUANTITY_WITH_BILLING,
    REASON_AMOUNT_DUE_BELOW_MINIMUM,
    REASON_UNKNOWN_PAYMENT_METHOD,
    REASON_PAYMENT_METHOD_NOT_ALLOWED,
    REASON_NO_PAYMENT_METHODS,
    REASON_QUOTE_IS_EMPTY,
    REASON_QUOTE_NOT_PROVISIONED,
    REASON_QUOTE_ALREADY_ACCEPTED,
    REASON_IRREVERSIBLE_AFTER_ACCEPTANCE,
    REASON_CHARGE_ALREADY_RECORDED,
    REASON_PAYMENT_REQUIRES_ACCEPTANCE,
    REASON_TAX_ID_LIMIT_REACHED,
    REASON_TAX_ID_EMPTY,
    REASON_INVALID_EFFECTIVE_DATE,
    REASON_UNKNOWN_BILLING_FREQUENCY,
    REASON_UNKNOWN_COLLECTION_PROCESS,
    REASON_UNKNOWN_NET_PAYMENT_TERMS,
)

# --------------------------------------------------------------------------- #
# The sentences the rules come from
# --------------------------------------------------------------------------- #
#
# Quoted verbatim from the research's evidence list and user flow for section 11, so a reader
# can check a rule against the sentence rather than trusting the code's summary of it.

CLICKWRAP_QUOTE = (
    "Accept without signature ... This can be useful in situations where a formal signature "
    "isn't needed, such as for purchase orders (POs). This option can be used with or without "
    "online payments on the quote, and a contact doesn't need to be added to the quote."
)

WHOLE_NUMBER_QUANTITY_QUOTE = (
    "if the quote offers online payments or the Enable billing switch is on, line item "
    "quantities must be whole numbers."
)

MINIMUM_CHARGE_QUOTE = (
    "when using HubSpot payments or Stripe as your payment processing option, the total amount "
    "due must be more than $0.50, or the equivalent minimum of the settlement currency."
)

FIRST_INVOICE_QUOTE = (
    "The first invoice is also generated and sent to the buyer immediately after quote "
    "acceptance, regardless of its scheduled invoice date or due date."
)

SUBSEQUENT_INVOICE_QUOTE = (
    "Subsequent invoices are generated and sent according to the contract's billing schedule, "
    "10 days before their invoice dates"
)

IRREVERSIBLE_QUOTE = (
    "Quotes can't be voided or deleted after they have been accepted, e-signed, or marked as "
    "signed."
)

ONLINE_PAYMENT_ACCEPTANCE_QUOTE = (
    "online payments require an acceptance method of E-signature or Accept without signature. "
    "Print and sign isn't a valid acceptance method with online payments."
)

PER_LINE_FREQUENCY_QUOTE = (
    "Billing frequency and billing start date are per line item (custom date, delayed by days, "
    "delayed by months) which supports ramped pricing"
)

SET_UP_PAYMENT_QUOTE = (
    "Buyer: opens the link, clicks Accept (click-to-accept), then optionally Set up payment at "
    "the top of the quote; they can close and revisit the quote later to set up payment."
)

PAYMENT_TYPE_AUTOMATIC_QUOTE = (
    "HubSpot payments vs Stripe are swappable per account, and hs_payment_type is set "
    "automatically."
)

TAX_ID_QUOTE = "Buyers can add up to three tax IDs to the quote"

REQUEST_ACCEPTANCE_FROM_QUOTE = (
    "under Request acceptance from choose Do not specify, a specific contact, or added "
    "contacts. This option can be used with or without online payments on the quote, and a "
    "contact doesn't need to be added to the quote."
)

STORE_PAYMENT_METHOD_QUOTE = (
    "a 'store the buyer's payment method for future charges' checkbox exists"
)

AUTOMATED_SALES_TAX_QUOTE = "Automated sales tax will be calculated when the quote is created"

#: What this workflow does not own, stated on the page rather than only in a docstring.
NOT_OWNED = {
    "quote_authoring": "WF-086 authors the quote and its line items; this workflow reads them.",
    "e_signature": "WF-095 collects acceptance by e-signature; this workflow is the clickwrap path.",
    "connected_cpq": (
        "The Connected CPQ single-contract-and-order path is the issue's out-of-scope clause; "
        "this workflow ships the subscription and invoice path."
    ),
    "payment_settlement": (
        "No money moves in this build. The charge row records the processor's outcome; a real "
        "HubSpot or Stripe integration settles it."
    ),
}


#: The minimum charge for a currency, as a plain float, for the vocabulary endpoint.
def minimum_charge(currency: str | None = None) -> float:
    """The minimum charge for a currency, falling back to the USD figure the research states."""

    code = (currency or DEFAULT_CURRENCY).strip().upper()
    return float(MINIMUM_CHARGE_BY_CURRENCY.get(code, MINIMUM_CHARGE_USD))


def vocabulary() -> dict[str, Any]:
    """The whole published vocabulary, as one JSON object.

    Built here rather than in the engine so every word has exactly one definition, and the
    engine can hand this straight to the endpoint without restating a constant.
    """

    return {
        "acceptance_methods": list(ACCEPTANCE_METHODS),
        "acceptance_method_labels": dict(ACCEPTANCE_METHOD_LABELS),
        "online_payment_acceptance_methods": list(ONLINE_PAYMENT_ACCEPTANCE_METHODS),
        "acceptance_recipient_modes": list(ACCEPTANCE_RECIPIENT_MODES),
        "clickwrap_anonymous_buyer": CLICKWRAP_ANONYMOUS_BUYER,
        "payment_methods": list(PAYMENT_METHODS),
        "payment_method_labels": dict(PAYMENT_METHOD_LABELS),
        "default_payment_methods": list(DEFAULT_PAYMENT_METHODS),
        "payment_types": list(PAYMENT_TYPES),
        "payment_type_labels": dict(PAYMENT_TYPE_LABELS),
        "payment_status_pending": PAYMENT_STATUS_PENDING,
        "collection_processes": list(COLLECTION_PROCESSES),
        "collection_process_labels": dict(COLLECTION_PROCESS_LABELS),
        "net_payment_terms": list(NET_PAYMENT_TERMS),
        "default_net_payment_terms": DEFAULT_NET_PAYMENT_TERMS,
        "billing_frequencies": list(BILLING_FREQUENCIES),
        "billing_frequency_labels": dict(BILLING_FREQUENCY_LABELS),
        "billing_period_months": dict(BILLING_PERIOD_MONTHS),
        "subsequent_invoice_periods": SUBSEQUENT_INVOICE_PERIODS,
        "effective_date_modes": list(EFFECTIVE_DATE_MODES),
        "effective_date_mode_labels": dict(EFFECTIVE_DATE_MODE_LABELS),
        "charge_outcomes": list(CHARGE_OUTCOMES),
        "charge_outcome_labels": dict(CHARGE_OUTCOME_LABELS),
        "activities": list(ACTIVITIES),
        "activity_labels": dict(ACTIVITY_LABELS),
        "invoice_kinds": list(INVOICE_KINDS),
        "invoice_statuses": list(INVOICE_STATUSES),
        "invoice_lead_days": INVOICE_LEAD_DAYS,
        "subscription_statuses": list(SUBSCRIPTION_STATUSES),
        "minimum_charge_usd": MINIMUM_CHARGE_USD,
        "minimum_charge_by_currency": dict(MINIMUM_CHARGE_BY_CURRENCY),
        "default_currency": DEFAULT_CURRENCY,
        "tax_id_limit": TAX_ID_LIMIT,
        "quote_properties_written": list(QUOTE_PROPERTIES_WRITTEN),
        "owned_collections": list(OWNED_COLLECTIONS),
        "read_only_collections": list(READ_ONLY_COLLECTIONS),
        "reason_codes": list(REASON_CODES),
        "evidence": {
            "clickwrap": CLICKWRAP_QUOTE,
            "whole_number_quantity": WHOLE_NUMBER_QUANTITY_QUOTE,
            "minimum_charge": MINIMUM_CHARGE_QUOTE,
            "first_invoice": FIRST_INVOICE_QUOTE,
            "subsequent_invoice": SUBSEQUENT_INVOICE_QUOTE,
            "irreversible": IRREVERSIBLE_QUOTE,
            "online_payment_acceptance": ONLINE_PAYMENT_ACCEPTANCE_QUOTE,
            "per_line_frequency": PER_LINE_FREQUENCY_QUOTE,
            "set_up_payment": SET_UP_PAYMENT_QUOTE,
            "payment_type_automatic": PAYMENT_TYPE_AUTOMATIC_QUOTE,
            "tax_id": TAX_ID_QUOTE,
            "request_acceptance_from": REQUEST_ACCEPTANCE_FROM_QUOTE,
            "store_payment_method": STORE_PAYMENT_METHOD_QUOTE,
            "automated_sales_tax": AUTOMATED_SALES_TAX_QUOTE,
        },
        "not_owned": dict(NOT_OWNED),
    }
