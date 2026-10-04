# WF-054 design question for Jev

Workflow: WF-054, Distribute bookings across a team by round robin.
Source: docs/research/digital-sales-room-workflows/wf/WF-054.md, GitHub issue 125.

## The gap in the research

The data flow reads: "team membership + weights/credits -> distribution algorithm ->
union/intersection of member calendars -> slot list -> booking".

The research names union and intersection but does not say which applies to which
round robin mode. The issue asks the implementer to derive it and record the
derivation rather than assume it.

What the research does fix:

- "either strict (equal turns) or flexible (weighted by availability)".
- "Chili Piper evaluates the distribution and returns a single combined
  availability window." One window, for both modes.

## State of the build

Files added, and no shared file touched:

- backend/dsr/round_robin/errors.py
- backend/dsr/round_robin/timeutil.py
- backend/dsr/round_robin/vocabulary.py
- backend/dsr/round_robin/teams.py
- backend/dsr/round_robin/availability.py
- backend/dsr/round_robin/selection.py
- backend/dsr/round_robin/credits.py
- backend/dsr/round_robin/engine.py
- backend/dsr/round_robin/inferences.py
- backend/dsr/round_robin/__init__.py
- backend/dsr/features/wf054_round_robin_booking.py on prefix /api/wf054
- backend/tests/test_wf054.py
- backend/tests/test_wf054_http.py
- frontend/src/features/wf-054-round-robin-booking/

Records live in four collections, all ordinary JSON in records.data:

- round_robin_team: the team and its members, each member carrying
  licensed, calendar_connected, and availability.
- round_robin_distribution: the reusable asset. Carries team_ref, mode
  (strict or flexible), credit_back_on_no_show, cursor, turn, and the
  interval and duration. Carries no per-booking state beyond the cursor and
  the turn, so the same Distribution context can be reused for reassignment.
- round_robin_route: one opened distribution evaluation, holding the offered
  slots, the chosen member, and the credit it consumed.
- round_robin_booking: the booked meeting.
- round_robin_no_show: the admin's no-show decision and the credit it gave back.

## Proposed calendar rule

Strict mode combines member calendars with a union. A prospect on a strict team
link may pick any time at least one member is free, and the distribution decides
who takes it. Intersecting would offer only times every member is free, which
shrinks the window every booking and makes a strict rotation impossible to keep
warm on a busy team.

Flexible mode combines member calendars with a weighted choice over availability.
The weighting is already sourced: "flexible (weighted by availability)". A member
with more free time is offered proportionally more of the combined window. It is
not an intersection, because an intersection ignores availability volume
entirely, which is the only thing "weighted by availability" measures.

Licensing is a hard gate, not a warning, before either operation runs:
"if any prospects match to an unlicensed user, they will not be able to book a
meeting and route to the Not Scheduled path." An unlicensed member is excluded
from assignment. An unlicensed member is still listed on the team, with the
reason.

## Proposed selection rule

Strict: pick the eligible member with the lowest (credits consumed, turns taken)
pair, ties broken by team member order. Equal turns means the cursor advances by
one per booking and no member is chosen twice before every other member has had
a turn.

Flexible: pick the eligible member whose share of the combined free time is
largest among those not yet at their turn in the current cycle, weighted by
credits consumed. A member with no free time in the window has weight zero and is
never chosen.

Credits: a booking consumes one credit on the chosen member. A no-show returns
that credit when the distribution sets credit_back_on_no_show. The flag is
sourced: "if the Distribution associated with the meeting is set to credit back
assignees for No-Shows".

## Requested decision

Which calendar rule belongs to which mode?