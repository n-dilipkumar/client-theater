"""WF-098: expire a quote and auto-send buyer reminders.

The researched workflow, in full. A super admin sets a default expiration period between
1 and 365 days and a reminder schedule of independent rules, each counting either days
after the quote was sent or days before it expires (steps 1 and 2). A seller sets a
specific expiration date, a label, or turns the switch off (step 3). A past effective date
is legal and turns the expiration date into the buyer's sign-by deadline (step 4). A
dispatch evaluates every rule and sends at the configured offset in the account's time
zone, and the expiry check moves an unaccepted quote to ``expired`` (step 5).

What the contract meant for this build
--------------------------------------

**The prefix is ``/api/wf-098`` and every route is room-scoped.** The research's join key
is the quote's send or publish timestamp and its expiration date, and the room is what a
seller is looking at when they ask which of their quotes is about to expire.

**``source`` comes from the route.** Every write passes a string built from
``router.prefix``, so the audit row names the route that served it. A hardcoded URL inside
a domain method is a defect, which is why every writing method on
:class:`~dsr.quoting_proposals.quote_expiry_engine.QuoteExpiryEngine` takes ``source`` as
a required keyword.

**One handler for the whole error hierarchy.**
:class:`~dsr.quoting_proposals.quote_expiry_rules.QuoteExpiryRefusal` is the base of
every refusal the rules raise and each carries its own ``status`` and ``code``, so one
handler answers 422 for a bad default window and 409 for a quote whose acceptance is
closed. The not-found types are claimed separately and they are this feature's own types,
because a feature may only map error types it raises itself.

**The two jobs are routes, not threads.** The research describes a background job and a
scheduled dispatch and says nothing about what drives either. That question was put to
Jev before implementation as audit ``jev-20261004T225135-29568-95339``, which selected
two explicit POST routes over a movable clock at confidence 1.00. A thread would need a
clock no test can move and would write audit rows naming no route.

**The domain module sits in the existing package.** Four new modules live in
``dsr/quoting_proposals/``, which already exists for this domain, and its five existing
files are untouched. That was decided with Jev as audit
``jev-20261004T225135-29568-95043`` at confidence 1.00.

**Nothing here claims a message left this product.** The research names HubSpot
transactional email and the PandaDoc auto-reminder endpoints. This product sends no HTTP
and holds no email credential, so the dispatch records the decision and reports
``sent_by_this_product: False``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.quoting_proposals import (
    quote_expiry_inferences as inferences,
    quote_expiry_vocabulary as vocab,
)
from dsr.quoting_proposals.quote_expiry_engine import NO_SCHEDULE_WRITE_API, QuoteExpiryEngine
from dsr.quoting_proposals.quote_expiry_rules import (
    QuoteExpiryRefusal,
    QuoteNotEditable,
    QuoteNotFound,
    ReminderRuleNotFound,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-098-expire-a-quote-and-auto-send-buyer-reminders",
    "ticket": "WF-098",
    "name": "Expire a quote and auto-send buyer reminders",
    "description": (
        "Give every quote an expiration date: a default window of 1 to 365 days, a "
        "per-quote date, a label, or the switch turned off. Dispatch reminder emails at "
        "either a days-after-send or a days-before-expiry offset, at the configured send "
        "time in the account time zone. A quote the buyer did not accept, e-sign or mark "
        "signed by its deadline becomes expired and the buyer can no longer accept it. An "
        "accepted quote survives its deadline. An expired quote can still be downloaded, "
        "cloned, voided or archived, and resending counts as a new send."
    ),
}

router = APIRouter(prefix="/api/wf-098", tags=["wf098"])


def get_engine(store: RecordStore = StoreDep) -> QuoteExpiryEngine:
    """A :class:`QuoteExpiryEngine` over the process-wide audited store.

    Built per request rather than held on ``app.state``, because putting it there is the
    edit to the shared ``dsr/api.py`` this feature host exists to make unnecessary. The
    engine holds a store handle and a clock and nothing else, so a test can build one
    directly and move the clock by hand.
    """

    return QuoteExpiryEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _quote_error(request: Request, exc: QuoteExpiryRefusal) -> JSONResponse:
    """A domain refusal, answered with the status and code the refusal carries.

    One handler for the hierarchy, because a default window outside 1 to 365 days and a
    quote whose acceptance is closed are both this package's errors and only one of them is
    about a value the caller sent.
    """

    return JSONResponse(
        status_code=exc.status,
        content={
            "error": exc.code,
            "detail": exc.detail,
            "status": exc.status,
            "errors": exc.errors,
        },
    )


def _quote_not_editable(request: Request, exc: QuoteNotEditable) -> JSONResponse:
    """A change asked of a quote whose deadline has already passed.

    Its own handler because the remediation differs from a refusal: the caller did not send
    a bad value, the quote is past the point where its date can move, and the fix is a new
    send.
    """

    return JSONResponse(status_code=409, content=exc.to_dict())


def _quote_not_found(request: Request, exc: QuoteNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "unknown_quote", "detail": str(exc), "status": 404},
    )


def _rule_not_found(request: Request, exc: ReminderRuleNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "unknown_reminder_rule", "detail": str(exc), "status": 404},
    )


EXCEPTION_HANDLERS = {
    QuoteExpiryRefusal: _quote_error,
    QuoteNotEditable: _quote_not_editable,
    QuoteNotFound: _quote_not_found,
    ReminderRuleNotFound: _rule_not_found,
}


# --------------------------------------------------------------------------- #
# Vocabulary and inferences
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every published term, served as data.

    The 1-to-365-day window, the two reminder offsets and the vendor's own label for each,
    the three acceptance methods, the three surviving buyer actions, the eight quote
    states, the send count, the void and archive consequences, the skip reasons, and the
    sentences that say what expiry is not. A page renders its badges and its copy from this
    rather than from a list compiled into the page, so a rule changed here reaches every
    client at once.
    """

    return vocab.catalogue()


@router.get("/inferences")
def inferences_route() -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The specification fixes the numbers and the surfaces and says almost nothing about the
    mechanism: who sweeps the expiry, who dispatches the reminder, what the reminder body
    says, how a buyer's action is time-checked. Those are collected here, named and served,
    rather than left as comments in a function body.
    """

    return inferences.register()


@router.get("/decisions/{decision_id}")
def read_decision(decision_id: str) -> dict[str, Any]:
    """One recorded decision, so a reviewer can read a single one without the whole list."""

    entry = inferences.describe_one(decision_id)
    if entry is None:
        return {"found": False, "id": decision_id, "count": inferences.count()}
    return {"found": True, **entry}


# --------------------------------------------------------------------------- #
# Step 1: the default expiration period
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/settings")
def read_settings(room_id: str, engine: QuoteExpiryEngine = EngineDep) -> dict[str, Any]:
    """The account settings, with the researched defaults filled in.

    A room with no settings row still answers, and every field that was a default rather
    than a stored value is reported, so a reader can tell a default from something an admin
    set.
    """

    return engine.settings(room_id)


@router.put("/rooms/{room_id}/settings")
def save_settings(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Set the default expiration period, the account time zone, the send time, the toggle.

    "Set a default expiration period for quotes... enter a default expiration time period
    between 1 and 365 days". The bound is checked here, which is where the research says the
    number is entered, and it is checked before a row is written, so a rejected setting
    leaves nothing behind.

    A payload with no ``default_expiration_days`` clears the default, which is a real
    operation: new quotes then stop inheriting a window. Sending ``null`` does the same,
    and both are distinguishable from a payload that never mentioned the field.
    """

    return engine.save_settings(
        room_id, payload, actor=actor, source=f"PUT {router.prefix}/rooms/{{room_id}}/settings"
    )


# --------------------------------------------------------------------------- #
# Step 2: the reminder schedule
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/reminder-rules")
def list_rules(room_id: str, engine: QuoteExpiryEngine = EngineDep) -> dict[str, Any]:
    """Every reminder rule on this account, oldest first.

    "**+ Add reminder** / delete icon to manage several" and "Multiple independent reminder
    rules" both say a rule is its own addressable thing, so this is a list of rows rather
    than a list inside the settings.
    """

    rows = engine.rules_for(room_id)
    return {
        "room_id": room_id,
        "count": len(rows),
        "offset_kinds": [
            {"kind": kind, "label": vocab.OFFSET_LABELS[kind]} for kind in vocab.OFFSET_KINDS
        ],
        "rules": rows,
        "gap": NO_SCHEDULE_WRITE_API,
    }


@router.post("/rooms/{room_id}/reminder-rules", status_code=201)
def add_rule(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Add one reminder rule.

    "under *Reminder schedule* set the number of days and choose **Days after sending
    quote** or **Days before expiration date** to **+ Add reminder**". Two rules with the
    same number of days are still two rules and both fire, because the research calls the
    rules independent.
    """

    return engine.add_rule(
        room_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/reminder-rules",
    )


@router.get("/rooms/{room_id}/reminder-rules/{rule_id}")
def read_rule(room_id: str, rule_id: str, engine: QuoteExpiryEngine = EngineDep) -> dict[str, Any]:
    """One rule, with the count of reminders it has dispatched."""

    return engine.rule_view(room_id, rule_id)


@router.patch("/rooms/{room_id}/reminder-rules/{rule_id}")
def update_rule(
    room_id: str,
    rule_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Move a rule's days, its offset kind, its label, or turn it off.

    A patch, so changing one number does not require resending the whole rule. The
    response names the fields that changed.
    """

    return engine.update_rule(
        room_id,
        rule_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/rooms/{{room_id}}/reminder-rules/{{rule_id}}",
    )


@router.delete("/rooms/{room_id}/reminder-rules/{rule_id}")
def delete_rule(
    room_id: str,
    rule_id: str,
    actor: str | None = Query(default=None),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Delete one rule, by the id the researched settings screen names.

    The ledger rows the rule produced are kept. They record that a reminder went out, and
    a seller asking "did my buyer get that nudge" needs the answer after the rule is gone.
    """

    return engine.delete_rule(
        room_id,
        rule_id,
        actor=actor,
        source=f"DELETE {router.prefix}/rooms/{{room_id}}/reminder-rules/{{rule_id}}",
    )


@router.get("/rooms/{room_id}/reminder-rules/{rule_id}/preview")
def preview_reminder(
    room_id: str,
    rule_id: str,
    quote_id: str | None = Query(default=None),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """The reminder a rule would send, without sending it.

    "**Preview reminder email**" is one of the researched product surfaces, so it is a
    route. Without a quote id the first quote in the room is used, because the settings
    screen previews against whatever is on screen. Every field the text was composed from
    is listed beside it, so a reader can see which part came from where.
    """

    return engine.reminder_preview(room_id, rule_id, quote_id)


# --------------------------------------------------------------------------- #
# Step 3: the tracked quote and its three expiration controls
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/quotes", status_code=201)
def create_quote(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Track a quote, with an expiration date, a label, or the switch off.

    "under *Expiration date* click the **date picker** to set a specific date, edit the
    **Label**, or toggle the **Expiration date** switch off."

    No date and no default is a valid quote that never expires. That is not a degraded case
    and it is not answered with a substituted date, because a switch a seller turned off
    must not be overridden by a default.
    """

    return engine.create_quote(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{{room_id}}/quotes"
    )


@router.get("/rooms/{room_id}/quotes")
def list_quotes(
    room_id: str,
    state: str | None = Query(default=None, description="filter by quote state"),
    expiring_soon: bool = Query(
        default=False, description="only quotes inside the expiring-soon window"
    ),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Every tracked quote in this room, newest first.

    "status filters for \"expiring soon\"" is a researched surface, so it is a query
    parameter rather than something for the client to compute from the raw list. The window
    is served in the vocabulary because the research names the filter without saying how
    soon is soon.
    """

    rows = engine.quotes(
        room_id,
        state=state,
        expiring_within_days=vocab.EXPIRING_SOON_DAYS if expiring_soon else None,
    )
    return {
        "room_id": room_id,
        "count": len(rows),
        "expiring_soon_days": vocab.EXPIRING_SOON_DAYS,
        "quotes": rows,
    }


@router.get("/rooms/{room_id}/quotes/{quote_id}")
def read_quote(
    room_id: str,
    quote_id: str,
    tz: str | None = Query(default=None, description="read the deadline in this timezone"),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """One quote, as the API returns it.

    Read on a terminal quote, deliberately: "An expired quote can still be downloaded,
    cloned, voided or archived." ``tz`` renders the deadline in one account's timezone
    without changing the instant the expiry check compares.
    """

    return engine.quote_view(quote_id, tz_name=tz)


@router.put("/rooms/{room_id}/quotes/{quote_id}/expiration")
def set_expiration(
    room_id: str,
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Set the date, edit the label, or turn the switch off.

    Three controls and three fields. Each is recognised by its own key, so a caller that
    only sends the label does not clear the date by omission, and an explicit ``null`` on
    the date clears it deliberately.

    Refused with 409 once the quote is expired, voided or archived. The deadline of a quote
    that already met it cannot be moved, or the Expired state would be terminal in name
    only.
    """

    return engine.set_expiration(
        quote_id,
        payload,
        actor=actor,
        source=f"PUT {router.prefix}/rooms/{{room_id}}/quotes/{{quote_id}}/expiration",
    )


@router.post("/rooms/{room_id}/quotes/{quote_id}/send", status_code=201)
def send_quote(
    room_id: str,
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Send or publish the quote, and count the send.

    "resending counts as a new send (consuming e-signature quota again)". Both counters go
    up, so a resend is visibly a new send rather than a touch, and the "days after sending
    quote" rules count from the new send instant. A "days before expiration date" rule does
    not move, because it counts from the expiration date.
    """

    return engine.send_quote(
        quote_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/quotes/{{quote_id}}/send",
    )


@router.post("/rooms/{room_id}/quotes/{quote_id}/acceptance", status_code=201)
def record_acceptance(
    room_id: str,
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Record what the buyer did, and when.

    "if the buyer has not accepted/e-signed/marked-signed by the expiration date". The
    action, the method and the instant are all stored, because the survival rule reads the
    instant and the three methods each have their own rule.

    Refused on an expired quote. "the buyer loses the ability to accept" is the one thing
    expiry takes away.
    """

    return engine.record_acceptance(
        quote_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/quotes/{{quote_id}}/acceptance",
    )


@router.get("/rooms/{room_id}/quotes/{quote_id}/can-accept")
def can_accept(
    room_id: str,
    quote_id: str,
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """May this buyer accept right now, and why not if not.

    A ``GET`` because it decides nothing and writes nothing. A buyer who arrives early is
    not refused, so the answer is a report, and it names the reason the refusal would.
    """

    return engine.can_accept(quote_id)


@router.post("/rooms/{room_id}/quotes/{quote_id}/void", status_code=201)
def void_quote(
    room_id: str,
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Deactivate the quote link URL, and nothing else.

    "**Void:** ... The quote link URL will be deactivated." One consequence and one field.
    The quote stays readable and downloadable.
    """

    return engine.void_quote(
        quote_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/quotes/{{quote_id}}/void",
    )


@router.post("/rooms/{room_id}/quotes/{quote_id}/archive", status_code=201)
def archive_quote(
    room_id: str,
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Unpublish the quote, hide it from the index, and block buyer access.

    "**Archive:** ... The quote is unpublished, hidden from the default index page view,
    and prevents buyers from accessing it." Three consequences and three fields, because
    they are separately observable.
    """

    return engine.archive_quote(
        quote_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/quotes/{{quote_id}}/archive",
    )


# --------------------------------------------------------------------------- #
# Step 5: the dispatch and the expiry check
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/reminders", status_code=201)
def dispatch_reminders(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Evaluate every due rule for every quote in this room, and record every decision.

    "a scheduled job evaluates sent/published quotes relative to that date and dispatches
    reminder emails at the configured offsets".

    Both offsets are computed, each from its own anchor: days from the send or publish
    instant, and days back from the expiration date. The hour of day is the account's
    reminder send time read in the account's time zone.

    Sends and skips are both rows, and the response says plainly that this product sends no
    message: the dispatch records the decision and an integration delivers it.
    """

    return engine.dispatch_reminders(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{{room_id}}/reminders"
    )


@router.get("/rooms/{room_id}/reminders")
def list_reminders(
    room_id: str,
    quote_id: str | None = Query(default=None),
    rule_id: str | None = Query(default=None),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """The reminder ledger, newest first. Sends and skips alike.

    A skip is a row for the same reason a send is: each researched suppression rule is easy
    to get wrong, and a skip nobody can see is a skip nobody can verify.
    """

    rows = engine.reminders(room_id, quote_id=quote_id, rule_id=rule_id)
    return {
        "room_id": room_id,
        "count": len(rows),
        "sent": sum(1 for row in rows if row.get("outcome") == vocab.OUTCOME_SENT),
        "skipped": sum(1 for row in rows if row.get("outcome") == vocab.OUTCOME_SKIPPED),
        "delivery": {
            "sent_by_this_product": False,
            "note": (
                "This product records the reminder decision and sends no message. An "
                "integration consumes this ledger and delivers."
            ),
        },
        "reminders": rows,
    }


@router.post("/rooms/{room_id}/expiry-check", status_code=201)
def check_expiry(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Expire every quote in this room whose deadline has passed and whose buyer has not acted.

    "If a buyer hasn't accepted or signed a quote by the expiration date, it'll expire."

    Five things it does not do, each a researched sentence: it deletes nothing, it leaves a
    quote with the switch off alone, it leaves an already-closed quote alone so a second
    pass cannot tell a buyer twice, it leaves a quote short of its deadline alone, and it
    leaves a quote the buyer accepted or signed in time alone.
    """

    return engine.check_expiry(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{{room_id}}/expiry-check"
    )


@router.get("/rooms/{room_id}/activities")
def list_activities(
    room_id: str,
    quote_id: str | None = Query(default=None),
    activity: str | None = Query(default=None),
    engine: QuoteExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """The quote activity log, newest first.

    "reminder + expiration events are exposed as quote activities that can drive
    workflows". They are rows rather than log lines so a workflow has something to read,
    and the expiry transition writes the activity name the research quotes verbatim.
    """

    rows = engine.activities(room_id, quote_id=quote_id, activity=activity)
    return {
        "room_id": room_id,
        "count": len(rows),
        "types": list(vocab.ACTIVITY_TYPES),
        "activities": rows,
    }


@router.get("/rooms/{room_id}/summary")
def summary(room_id: str, engine: QuoteExpiryEngine = EngineDep) -> dict[str, Any]:
    """Counts for the room's header, and the invariants beside them.

    Read back from the store rather than accumulated, so the header cannot describe a state
    the store does not hold. The invariants are here because they are the things a reader
    looks for and does not find on a page: expiry keeps the quote, an accepted quote
    survives its deadline, a resend is a new send, and a switch that is off never closes.
    """

    return engine.summary(room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The feature's own prefix, written out a second time on purpose. The demo data's audit
#: rows have to name the same routes the router serves, and a change to the prefix has to be
#: made deliberately in both places.
PREFIX = "/api/wf-098"

SETTINGS_SOURCE = f"PUT {PREFIX}/rooms/{{room_id}}/settings"
RULE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/reminder-rules"
QUOTE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/quotes"
EXPIRATION_SOURCE = f"PUT {PREFIX}/rooms/{{room_id}}/quotes/{{quote_id}}/expiration"
SEND_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/quotes/{{quote_id}}/send"
ACCEPTANCE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/quotes/{{quote_id}}/acceptance"
REMINDER_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/reminders"
EXPIRY_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/expiry-check"
VOID_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/quotes/{{quote_id}}/void"
ARCHIVE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/quotes/{{quote_id}}/archive"

DAY = 86400


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Six quotes and the states that are not all successes.

    The rows are produced by running the real :class:`QuoteExpiryEngine`, so the demo cannot
    show a shape this workflow would not produce. It is deliberately mixed, because a demo
    of only green teaches a reviewer nothing:

    * **one expired quote** nobody acted on, so ``Quote expired`` is a row and the buyer has
      genuinely lost the ability to accept;
    * **one quote accepted before its deadline**, so the survival rule is a row rather than
      a claim;
    * **one quote countersigned but never accepted**, which is the case the research's one
      hard sentence names, so the page shows it still expiring;
    * **one sent quote with the switch off**, so "never expires" is visible;
    * **one voided quote** and **one archived quote**, so the two researched consequences
      are distinguishable from each other and from expiry;
    * **one quote with a past effective date**, so the sign-by-deadline reading is visible;
    * a **reminder skipped because its rule already fired**, because each researched
      suppression rule is a row a reviewer most wants to see.

    Every character in the returned string is encodable by cp1252. The seeder prints it on
    a Windows console, and a single RIGHTWARDS ARROW in one recovered feature broke the
    whole seed.
    """

    from datetime import datetime, timezone

    store = RecordStore(db)
    clock = context.get("now")
    if clock is None:
        clock = datetime.now(timezone.utc)
    base = int(clock.timestamp())
    engine = QuoteExpiryEngine(store, now=lambda: clock)

    given: list[tuple[str, str]] = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]
    room_ids = [room_id for room_id, _ in given if store.get(room_id) is not None][:2]
    names = [
        "Northwind Traders - quote expiry",
        "Contoso Health - quote expiry",
        "Fabrikam Logistics - quote expiry",
    ]
    created = 0
    for index in range(2):
        if index < len(room_ids):
            continue
        name = names[index]
        record = store.create("room", {"name": name, "account": name.split(" - ")[0]}, actor="dana")
        room_ids.append(record["id"])
        created += 1
    room_id = room_ids[0]

    def at(days: float) -> str:
        moment = datetime.fromtimestamp(base + int(days * DAY), tz=timezone.utc)
        return moment.isoformat(timespec="milliseconds")

    # -- the account settings and the schedule ------------------------------- #
    engine.save_settings(
        room_id,
        {
            vocab.DEFAULT_EXPIRATION_DAYS: 30,
            vocab.ACCOUNT_TIMEZONE: "UTC",
            vocab.REMINDER_SEND_TIME: "09:00",
            vocab.AUTOMATED_REMINDERS_ENABLED: True,
        },
        actor="dana",
        source=SETTINGS_SOURCE,
    )
    engine.add_rule(
        room_id,
        {"offset_kind": vocab.OFFSET_AFTER_SEND, "days": 3, "label": "Three days after we sent it"},
        actor="dana",
        source=RULE_SOURCE,
    )
    engine.add_rule(
        room_id,
        {
            "offset_kind": vocab.OFFSET_BEFORE_EXPIRY,
            "days": 2,
            "label": "Two days before it expires",
        },
        actor="dana",
        source=RULE_SOURCE,
    )

    def recipients(address: str) -> list[dict[str, str]]:
        return [{"email": address, "name": address.split("@")[0]}]

    # -- one nobody acted on: it expires ------------------------------------- #
    missed = engine.create_quote(
        room_id,
        {
            "title": "Enterprise platform - Northwind",
            "seller_email": "dana@northwind.example",
            "recipients": recipients("buyer@northwind.example"),
            vocab.EXPIRATION_DATE: at(1),
            vocab.EXPIRATION_LABEL: "Sign by",
            vocab.EFFECTIVE_DATE: at(-20),
        },
        actor="dana",
        source=QUOTE_SOURCE,
    )
    engine.send_quote(missed["id"], {}, actor="dana", source=SEND_SOURCE)

    # -- one accepted in time: it survives ------------------------------------ #
    accepted = engine.create_quote(
        room_id,
        {
            "title": "Renewal - Contoso Health",
            "seller_email": "dana@contoso.example",
            "recipients": recipients("buyer@contoso.example"),
            vocab.EXPIRATION_DATE: at(1),
            vocab.EFFECTIVE_DATE: at(-30),
        },
        actor="dana",
        source=QUOTE_SOURCE,
    )
    engine.send_quote(accepted["id"], {"publish": True}, actor="dana", source=SEND_SOURCE)
    engine.record_acceptance(
        accepted["id"],
        {"action": vocab.ACTION_ACCEPTED, "method": vocab.METHOD_CLICK_TO_ACCEPT},
        actor="buyer@contoso.example",
        source=ACCEPTANCE_SOURCE,
    )

    # -- one countersigned and never accepted: it still expires -------------- #
    countersigned = engine.create_quote(
        room_id,
        {
            "title": "Pilot - Fabrikam Logistics",
            "seller_email": "dana@fabrikam.example",
            "recipients": recipients("ops@fabrikam.example"),
            vocab.EXPIRATION_DATE: at(1),
            vocab.EFFECTIVE_DATE: at(-10),
        },
        actor="dana",
        source=QUOTE_SOURCE,
    )
    engine.send_quote(countersigned["id"], {}, actor="dana", source=SEND_SOURCE)
    engine.record_acceptance(
        countersigned["id"],
        {"action": vocab.ACTION_COUNTERSIGNED, "method": vocab.METHOD_PRINT_AND_SIGN},
        actor="ops@fabrikam.example",
        source=ACCEPTANCE_SOURCE,
    )

    # -- one with the switch off: it never expires ---------------------------- #
    switched_off = engine.create_quote(
        room_id,
        {
            "title": "Pilot scope - Fabrikam Logistics",
            "seller_email": "dana@fabrikam.example",
            "recipients": recipients("ops@fabrikam.example"),
            vocab.EXPIRATION_ENABLED: False,
            vocab.EXPIRATION_LABEL: "No deadline on this one",
        },
        actor="dana",
        source=QUOTE_SOURCE,
    )
    engine.send_quote(switched_off["id"], {}, actor="dana", source=SEND_SOURCE)

    # -- one voided and one archived: two different consequences -------------- #
    voided = engine.create_quote(
        room_id,
        {
            "title": "Superseded order form - Contoso Health",
            "seller_email": "dana@contoso.example",
            "recipients": recipients("buyer@contoso.example"),
            vocab.EXPIRATION_DATE: at(45),
            vocab.EFFECTIVE_DATE: at(-1),
        },
        actor="dana",
        source=QUOTE_SOURCE,
    )
    engine.send_quote(voided["id"], {"publish": True}, actor="dana", source=SEND_SOURCE)
    engine.void_quote(voided["id"], {}, actor="dana", source=VOID_SOURCE)

    archived = engine.create_quote(
        room_id,
        {
            "title": "Closed campaign - Northwind Traders",
            "seller_email": "dana@northwind.example",
            "recipients": recipients("buyer@northwind.example"),
            vocab.EXPIRATION_DATE: at(20),
            vocab.EFFECTIVE_DATE: at(-60),
        },
        actor="dana",
        source=QUOTE_SOURCE,
    )
    engine.send_quote(archived["id"], {"publish": True}, actor="dana", source=SEND_SOURCE)
    engine.archive_quote(archived["id"], {}, actor="dana", source=ARCHIVE_SOURCE)

    # -- one whose effective date is in the past: a sign-by deadline --------- #
    sign_by = engine.create_quote(
        room_id,
        {
            "title": "Q4 expansion - Northwind Traders",
            "seller_email": "dana@northwind.example",
            "recipients": recipients("buyer@northwind.example"),
            vocab.EFFECTIVE_DATE: at(-5),
            vocab.EXPIRATION_LABEL: "Sign by the end of the quarter",
        },
        actor="dana",
        source=QUOTE_SOURCE,
    )
    engine.send_quote(sign_by["id"], {}, actor="dana", source=SEND_SOURCE)

    # -- the first dispatch, four days after that quote was sent -------------- #
    later = datetime.fromtimestamp(base + 4 * DAY, tz=timezone.utc)
    dispatcher = QuoteExpiryEngine(store, now=lambda: later)
    first = dispatcher.dispatch_reminders(room_id, {}, actor="scheduler", source=REMINDER_SOURCE)

    # -- the expiry check on the same clock ----------------------------------- #
    # The three short-deadline quotes are past theirs and the rest are not. One was
    # accepted in time and one was only countersigned, so one survives and one does not.
    checked = QuoteExpiryEngine(store, now=lambda: later).check_expiry(
        room_id, {}, actor="scheduler", source=EXPIRY_SOURCE
    )

    # -- a second dispatch a moment later: the rule already fired is a skip ---- #
    again = QuoteExpiryEngine(store, now=lambda: later).dispatch_reminders(
        room_id, {}, actor="scheduler", source=REMINDER_SOURCE
    )

    room = dispatcher.summary(room_id)
    expired_ids = {row["quote_id"] for row in checked["expired"]}
    kept = room["accepted"] + room["signed"]

    return (
        f"{room['quotes']} quote(s) in one room: "
        f"{room['expired']} expired, {room['accepted']} accepted and {room['signed']} signed, "
        f"{room['voided']} voided, {room['archived']} archived, "
        f"{room['expiration_off']} with the Expiration date switch off and still open, "
        f"{room['sign_by_deadline']} reading its expiration date as a sign-by deadline, "
        f"{kept} quote(s) kept by the survival rule, "
        f"and {len(expired_ids)} actually expired by the check; "
        f"{first['sent']} reminder(s) sent at the 3-day-after-send lead, "
        f"{first['skipped']} skipped on the first pass, "
        f"{again['skipped']} skipped on the second pass because the rule already fired; "
        f"{room['expired_activities']} 'Quote expired' activity row(s) and "
        f"{room['reminder_activities']} 'Reminder sent' activity row(s); "
        f"{room['reminder_rules']} reminder rule(s), one counting from the send and one "
        f"back from the expiration date"
        + (f"; {created} room(s) created for the per-room states" if created else "")
        + f"; {len(given)} room(s) from the seeder"
    )
