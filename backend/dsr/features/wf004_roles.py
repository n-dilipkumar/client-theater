"""WF-004: invite buyers to a room with a role and an access expiry.

Ported from ``feature/WF-004-invite-buyers-to-a-room-with-a-role`` onto the
plugin host. The workflow's own work is in two modules the port took over from
the branch, renamed so this branch owns no path the other half of the pair
owns:

* :mod:`dsr.roles` — the domain. The role vocabulary, the delegation rule, the
  48-hour acceptance window, the end-of-day-UTC cut-off, and the confirmation
  guard on destructive changes. Pure over :class:`~dsr.store.RecordStore`; no
  framework, so it is unit-tested on its own with an injected clock.
* :mod:`dsr.roles_api` — the HTTP surface, an ``APIRouter`` this feature owns
  and the host mounts.

This module is what the branch could not express without editing shared files.
On the branch, three of this workflow's four integration points were writes to
files every other workflow also wrote:

* the route table, which was ``app.include_router(access_router)`` appended
  inside ``dsr/api.py``;
* the error mapping, which was an ``@app.exception_handler(AccessError)`` block
  in the same file;
* the demo rows, which were an edit to ``backend/seed.py``;
* and, on the frontend, the Share button and a *Viewing as* control inside
  ``frontend/src/pages/Rooms.jsx``, a file WF-001 also edits.

The first three are now this file and the fourth is a page in this feature's
own folder. Without that shape, WF-004 could not be merged without resolving a
conflict against every other workflow in the programme, because all of them
appended to the same handful of files.

Four deliberate departures from the branch, each required by the contract:

* **The prefix is ``/api/wf-004-invite-buyer``.** The branch served
  ``GET /api/rooms/{room_id}/access``, ``POST /api/rooms/{room_id}/invitations``
  and friends from the shared app. Those are core vocabulary several other
  workflows want, and the host refuses a feature that claims a concrete route
  already taken. The sibling branch in this pair, WF-015, serves the same
  ``rooms/{room_id}/access`` shape; under prefixes the two resolve by
  construction. The branch was never merged, so nothing external depended on the
  old paths.
* **The module is ``roles``, not ``access``.** See the note in
  :mod:`dsr.roles`. The collection names stay ``room_invitation`` and
  ``room_access`` because they are data, not code.
* **Every write is handed the path this router actually serves.** The branch
  hard-coded ``source=f"POST /api/rooms/{room_id}/invitations"`` and
  ``f"PATCH /api/access/{access_id}"`` inside the domain module, which is the
  defect the port brief calls out: the audit row stops naming a route anyone can
  call. ``source`` is now a required keyword on the four write methods and is
  built from ``router.prefix`` by :func:`dsr.roles_api._source`, so the two
  cannot drift.
* **Demo data lives here.** See :func:`seed`.

One thing the port could **not** carry over, and it is a finding rather than an
omission: ``pages/Rooms.jsx`` is where the research puts the Share action, next
to the room it shares, and WF-001 edits that file too. A feature must not own a
file another feature owns. The Share surface is therefore this feature's own
page rather than a button on the core rooms page, which also means the Share
button is reachable from the nav without anyone editing ``App.jsx``. Closing the
loop properly means a *share* affordance on the rooms page, and that is a
one-line change in a file this feature may not touch.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from dsr import roles
from dsr.roles_api import (
    EXCEPTION_HANDLERS,  # noqa: F401  (the host reads this)
    router,
)

__all__ = ["FEATURE", "router", "EXCEPTION_HANDLERS", "seed"]

FEATURE = {
    "id": "wf-004-invite-buyer",
    "ticket": "WF-004",
    "name": "Invite buyers to a room with a role",
    "description": (
        "Share a room by email: one role and one access-expiry date for the whole "
        "invitation, a 48-hour acceptance window, and a Who Has Access list where "
        "only the owner can hand out Room Collaborator and the owner can never be "
        "removed."
    ),
    "nav": [{"id": "invite-buyer", "label": "Invite buyers"}],
}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# The branch added this to ``backend/seed.py``, which is a shared file that ten
# of the first twelve workflows rewrote purely to add their own demo rows. The
# seeder calls the hook below instead, so the demo travels with the feature that
# needs it. A feature whose page is empty in the demo is a feature nobody can
# review, and this one has more than one way to be empty: with no grants there is
# no *Who Has Access* row, with no lapsed grant there is no seven-day banner, and
# with no pending invitation there is nothing to accept.

#: ``account`` as the core seeder labels it, to the people seeded into it. Each
#: entry is ``(email, role, horizon)`` where the horizon picks an expiry outcome
#: that makes one researched behaviour visible without any interaction.
#:
#: The mix is deliberate: ``soon`` produces a grant inside the seven-day
#: warning window, ``far`` produces a grant that is safely dated, and ``None``
#: produces the *No Expiration* row. One account deliberately has no grant at
#: all, so the join-on-send path and the join-on-accept path are both reachable
#: from the demo.
SEED_GRANTS: dict[str, tuple[tuple[str, str, Any], ...]] = {
    "Northwind Traders": (
        # Lapsing inside the window, so the banner is on screen and the
        # "Expiring in Nd" row label is doing something.
        ("a.buyer@northwind.example", "content_contributor", "soon"),
        ("b.buyer@northwind.example", "viewer", "far"),
        # No date at all: the *No Expiration* row the research documents.
        ("legal@northwind.example", "room_collaborator", None),
    ),
    "Contoso Health": (
        ("procurement@contoso.example", "viewer", "soon"),
        ("ciso@contoso.example", "content_contributor", None),
    ),
    "Fabrikam Logistics": (("ops@fabrikam.example", "content_contributor", "far"),),
    "Adventure Works": (("lead@adventure.example", "viewer", "far"),),
}

#: One invitation still in flight, so the 48-hour window is visible on load
#: rather than only after pressing Invite.
SEED_PENDING_INVITATION = ("new.buyer@northwind.example", "viewer", None)


def _expiry(now, horizon):
    """A calendar date for a horizon, or ``None`` for *No Expiration*."""
    if horizon == "soon":
        return (now + timedelta(days=3)).strftime("%Y-%m-%d")
    if horizon == "far":
        return (now + timedelta(days=120)).strftime("%Y-%m-%d")
    return None


def seed(db, context: dict[str, Any]) -> str:
    """Make the core demo dataset readable as a room other people can enter.

    Every write goes through the audited database, so running the seed twice
    produces a second complete set of audit rows rather than overwriting the
    first. The dates are derived from ``context["now"]`` rather than hard-coded,
    so "expiring within seven days" stays true however long after the code was
    written the demo is seeded.
    """
    room_ids: list[tuple[str, str]] = context["room_ids"]
    if not room_ids:
        return "no rooms to attach to"
    now = context["now"]

    grants = 0
    for room_id, account in room_ids:
        for email, role, horizon in SEED_GRANTS.get(account, ()):
            db.create(
                roles.ACCESS,
                {
                    "principal": email,
                    "principal_type": "email",
                    "role": role,
                    "access_valid_until": _expiry(now, horizon),
                    "source": "invitation",
                    # Seeded as joined-on-invite, which is what the research
                    # says happens to a recipient who already belongs to the
                    # room. The dialog labels that row "Joined on invite".
                    "joined_immediately": True,
                },
                room_id=room_id,
                actor="dana",
                source="seed",
            )
            grants += 1

    # One invitation still in flight, on the first room, so the acceptance
    # window is on screen the moment the page loads.
    email, role, horizon = SEED_PENDING_INVITATION
    sent_at = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    db.create(
        roles.INVITATIONS,
        {
            "email": email,
            "role": role,
            "access_valid_until": _expiry(now, horizon),
            "state": "pending",
            # Not a real secret. The field exists so a real deployment has
            # somewhere to put an emailed link; nothing consumes it here, and
            # a demo token that looked real would be worse than one that says so.
            "token": "seed-demo-token-not-a-real-secret",
            "sent_at": sent_at,
            "expires_at": (now + timedelta(hours=roles.INVITATION_TTL_HOURS))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "sent_by": "dana",
        },
        room_id=room_ids[0][0],
        actor="dana",
        source="seed",
    )

    return f"{grants} access grants, 1 pending invitation"
