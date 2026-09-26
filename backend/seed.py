#!/usr/bin/env python3
"""Seed the Digital Sales Room with a realistic demo dataset.

Run against a throwaway database for local development:

    DSR_DB_PATH=data/dsr.db python backend/seed.py

Every write goes through the audited store, so running this twice produces a
second, complete set of audit rows rather than overwriting the first.
"""

from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from dsr.access import INVITATION_TTL_HOURS  # noqa: E402
from dsr.db.audited import AuditedDatabase  # noqa: E402

ROOMS = [
    {
        "name": "Northwind Traders — Enterprise Evaluation",
        "account": "Northwind Traders",
        "stage": "evaluation",
        "owner": "dana",
        "seats": 40,
        "branding": {"theme": "dark", "accent": "#22c55e", "logo": "northwind.svg"},
        "integrations": ["salesforce", "zoom"],
        "expires_at": "2026-12-31",
    },
    {
        "name": "Contoso Health — Security Review",
        "account": "Contoso Health",
        "stage": "discovery",
        "owner": "sam",
        "seats": 12,
        "branding": {"theme": "light", "accent": "#0ea5e9"},
        "integrations": ["salesforce"],
        "requires_nda": True,
    },
    {
        "name": "Fabrikam Logistics — Renewal",
        "account": "Fabrikam Logistics",
        "stage": "negotiation",
        "owner": "dana",
        "seats": 25,
        "branding": {"theme": "dark", "accent": "#f59e0b"},
        "integrations": ["salesforce", "slack", "hubspot"],
        "renewal_date": "2026-11-15",
    },
    {
        "name": "Adventure Works — Pilot",
        "account": "Adventure Works",
        "stage": "closed",
        "owner": "sam",
        "seats": 8,
        "branding": {"theme": "dark", "accent": "#a855f7"},
        "integrations": ["teams"],
        "won_at": "2026-08-30",
    },
]

DOCUMENTS = [
    ("Enterprise Overview Deck", "deck", 24, True),
    ("Security & Compliance Pack", "pdf", 48, False),
    ("Pricing One-Pager", "pdf", 2, True),
    ("Implementation Roadmap", "pdf", 6, True),
    ("Customer Reference — Northwind", "video", 1, True),
    ("API Integration Guide", "pdf", 32, False),
    ("Contract Draft", "pdf", 18, False),
    ("Mutual Action Plan", "pdf", 4, True),
]

ACTIVITIES = [
    "viewed",
    "downloaded",
    "shared",
    "commented",
    "opened_link",
    "completed_section",
]

PEOPLE = [
    "a.buyer@northwind.example",
    "b.buyer@northwind.example",
    "procurement@contoso.example",
    "ops@fabrikam.example",
    "lead@adventure.example",
]

# WF-004 demo data. Each entry is (email, role, access_valid_until or None).
# The mix is deliberate: one lapsed date so the seven-day banner has something to
# count, one open-ended grant so the *No Expiration* row is visible, and one
# without any grant at all so the immediate-join-on-send path can be tried.
ROOM_ACCESS = {
    "Northwind Traders": [
        ("a.buyer@northwind.example", "content_contributor", "soon"),
        ("b.buyer@northwind.example", "viewer", "far"),
        ("legal@northwind.example", "room_collaborator", None),
    ],
    "Contoso Health": [
        ("procurement@contoso.example", "viewer", "soon"),
        ("ciso@contoso.example", "content_contributor", None),
    ],
    "Fabrikam Logistics": [
        ("ops@fabrikam.example", "content_contributor", "far"),
    ],
    "Adventure Works": [
        ("lead@adventure.example", "viewer", "far"),
    ],
}


def main() -> int:
    rng = random.Random(20260926)  # deterministic demo data
    db_path = os.environ.get("DSR_DB_PATH", str(ROOT / "data" / "dsr.db"))
    mirror = os.environ.get("DSR_AUDIT_DIR", str(ROOT / "data" / "audit"))

    # SQLite will not create the directory it is asked to write into, and the
    # seed script is documented as runnable against a fresh checkout.
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    db = AuditedDatabase(db_path, mirror_dir=mirror, actor="seed")
    print(f"seeding {db_path}")

    if db.count("room") > 0:
        print("database already has rooms; pass DSR_DB_PATH pointing at a fresh file to reseed")
        db.close()
        return 1

    now = datetime.now(timezone.utc)
    room_ids: list[tuple[str, str]] = []

    for spec in ROOMS:
        room = db.create("room", spec, actor=spec["owner"], source="seed")
        room_ids.append((room["id"], spec["account"]))
        print(f"  room      {spec['name']}")

    for index, (title, kind, pages, public) in enumerate(DOCUMENTS):
        room_id, account = room_ids[index % len(room_ids)]
        db.create(
            "document",
            {
                "title": title,
                "kind": kind,
                "pages": pages,
                "public": public,
                "views": rng.randint(4, 180),
                "avg_dwell_seconds": rng.randint(20, 400),
                "status": "published",
            },
            room_id=room_id,
            actor="dana",
            source="seed",
        )
    print(f"  documents {len(DOCUMENTS)}")

    for step in range(140):
        room_id, account = rng.choice(room_ids)
        happened = now - timedelta(minutes=rng.randint(0, 60 * 24 * 21))
        db.create(
            "activity",
            {
                "person": rng.choice(PEOPLE),
                "account": account,
                "action": rng.choice(ACTIVITIES),
                "target": rng.choice([d[0] for d in DOCUMENTS]),
                "seconds_on_page": rng.randint(3, 900),
                "device": rng.choice(["desktop", "mobile", "tablet"]),
                "country": rng.choice(["AU", "US", "GB", "DE", "SG"]),
                "occurred_at": happened.isoformat(timespec="seconds"),
            },
            room_id=room_id,
            actor="system",
            source="seed",
        )
    print("  activities 140")

    # WF-004: who has access, with a spread of expiry outcomes. Written through
    # the same audited store, so the Share dialog and the audit log agree.
    access_count = 0
    for room_id, account in room_ids:
        for email, role, horizon in ROOM_ACCESS.get(account, []):
            if horizon == "soon":
                expiry = (now + timedelta(days=3)).strftime("%Y-%m-%d")
            elif horizon == "far":
                expiry = (now + timedelta(days=120)).strftime("%Y-%m-%d")
            else:
                expiry = None
            db.create(
                "room_access",
                {
                    "principal": email,
                    "principal_type": "email",
                    "role": role,
                    "access_valid_until": expiry,
                    "source": "invitation",
                    "joined_immediately": True,
                },
                room_id=room_id,
                actor="dana",
                source="seed",
            )
            access_count += 1

    # One invitation still in flight, so the 48-hour window is visible on load.
    db.create(
        "room_invitation",
        {
            "email": "new.buyer@northwind.example",
            "role": "viewer",
            "access_valid_until": None,
            "state": "pending",
            "token": "seed-demo-token-not-a-real-secret",
            "sent_at": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "expires_at": (now + timedelta(hours=INVITATION_TTL_HOURS))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "sent_by": "dana",
        },
        room_id=room_ids[0][0],
        actor="dana",
        source="seed",
    )
    print(f"  access grants {access_count}, pending invitations 1")

    stats = db.stats()
    print(f"\ndone: {stats['records']} live records, {stats['audit_entries']} audit entries")
    print(f"by action: {stats['by_action']}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
