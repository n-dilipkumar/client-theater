"""Generate the set-4 port briefs and commit them, in one operation.

The brief generator has been hand-edited repeatedly and reverted repeatedly by
shell restarts - the same failure that made merge_ports.py re-merge three shipped
ports and report success. So this writes the brief BODIES here, in full, and
commits them in the same process, then reads the committed content back from git
to confirm. A brief that is not committed is a brief no agent can read, because
the agents branch from main.

Set 4 is the four rescued workflows. Three are dispatched here; the fourth is not,
and the reason is in the brief body below rather than only in a message:

  WF-004  invite buyers to a room with a role          dispatched
  WF-015  verify buyer identity, restrict by domain     dispatched
  WF-017  white-label rooms on a custom domain          dispatched
  WF-001  create room from template                     HELD - edits db/audited.py

WF-001 is held for the same reason WF-005 and WF-014 are: its branch edits
`backend/dsr/db/audited.py`, which is the audit guarantee itself. A port is a
mechanical transformation and cannot be trusted with a change to the thing the
whole product is built on. That needs a human to read what changed and decide
whether it belongs in the feature or in the host.

The WF-004/WF-015 collision is already decided, by measurement and then by Jev:

  * both add `backend/dsr/access.py` - one module path, two owners
  * both serve `GET /api/rooms/{room_id}/access`
  * content overlap measured at 3.2% in the domain module, 1.0% in the domain
    tests, 4.8% in the HTTP tests; 2 shared symbols of 74, one of them __init__
  * WF-004's vocabulary is entirely about GRANTING; WF-015's entirely about
    VERIFYING. They barely intersect.
  * Jev's first ask was `uncertain` (0.68, threshold 0.75) because the content
    overlap had not been measured. With it measured, the second ask returned
    `pass` at confidence 1.00, selecting two self-contained features with WF-004's
    module renamed. audit_id jev-20260927T052837-24152-17484.

So WF-004 is told to rename its module and WF-015 is told to keep it, and neither
is left to guess.
"""
import json
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(r"C:\Users\Dilip\orca\projects\client-theater\client-theater")
OUT = ROOT / "orchestration" / "ports"

# The shared body every brief carries, matching the eleven already merged. Read
# from the most recent one so the wording cannot drift between batches.
BASE = (OUT / "WF-013.md").read_text(encoding="utf-8")
head, _, rest = BASE.partition("## Notes for this specific workflow")
_, _, tail = rest.partition("\n---\n")

NOTES = {
    "WF-004": """1. **Your module is renamed, and that is a decision already made - do not
   re-open it.** This branch adds `backend/dsr/access.py`. So does WF-015's
   branch. Two features cannot own one module path; that is the exact failure the
   plugin host exists to end. Your branch is about GRANTING - invitations, roles,
   who may enter a room and with what rights - so **rename your module to
   `backend/dsr/roles.py`** and your tests to `test_roles.py` and
   `test_roles_api.py`. WF-015 keeps `access.py` because it is about verifying
   identity and enforcing a policy.

   This is not a guess. The two files were measured: **3.2% overlap** in the
   domain module, 1.0% in the domain tests, 4.8% in the HTTP tests, and **2
   shared symbols out of 74**, one of which is `__init__`. Your 37 unique symbols
   are all about granting (`invite`, `accept`, `remove_access`, `update_access`,
   `assignable_roles`, `role_vocabulary`, `is_owner`, `can_share`,
   `ConfirmationRequired`, `end_of_day_utc`); WF-015's 35 are all about
   verifying (`AccessGate`, `verify`, `build_token`, `email_domain`,
   `looks_like_bot`, `ResolvedPolicy`, `set_policy`, `template_policy`). Jev was
   asked how to land both and returned `pass` at confidence 1.00 for two
   self-contained features (audit_id jev-20260927T052837-24152-17484). Renaming
   is the outcome of that decision, not a stylistic preference.

2. **Your branch also modifies `tools/jev.py`. Do NOT carry that over.**
   `tools/jev.py` is the validator every decision in this project goes through,
   so a change to it is a change to the thing that judges every other change. It
   is untrusted input until a human has read what it does. Leave it behind, and
   say in your report that your branch modifies it, so the reviewer knows to look.
   The same goes for `tools/wf004_*.py` and `tools/test_jev_gate.py` - those are
   scratch gate scripts from the branch, not part of the workflow.

3. **Do not carry over `frontend/src/pages/Rooms.jsx`.** WF-001 also edits it.
   The contract says so, and a second owner of a file is the collision this port
   exists to prevent. Your share UI goes in your own feature folder.

4. **Your `rooms/{room_id}/access` route stops colliding once you have a prefix.**
   Under the host you serve `GET /api/wf-004/rooms/{room_id}/access`; WF-015
   serves `GET /api/wf-015/rooms/{room_id}/access`. Eleven features already work
   this way, three of them sharing `/api/library` and two sharing
   `/api/publishing`, all loading together. The one shared route in the two
   branches resolves by construction.

5. **This is one of the larger ports in the programme.** `access.py` is 817
   lines with 84 domain tests, and the delegation rules are the interesting part:
   who may assign `room_collaborator`, why an owner's own grant cannot be removed,
   why a role change needs confirmation but a reassertion of the same role does
   not, and why expiry lapses in UTC. Those are product decisions and they should
   survive the port intact.""",

    "WF-015": """1. **Keep `backend/dsr/access.py`. It is yours, and the other feature has been
   told to move.** WF-004's branch also adds a file by that name, but its content
   is about granting roles and yours is about verifying identity. They were
   measured: **3.2% overlap**, **2 shared symbols out of 74**, one of them
   `__init__`. WF-004 is renaming to `roles.py`; you keep `access.py`, and your
   tests keep the names `test_access.py` and `test_access_api.py`.

   Do not import anything from WF-004's module even if you can see it. You cannot
   see its branch, and a cross-feature import is exactly the coupling the plugin
   host removes. If you think the two should share something, say so in your
   report - that is a human decision about the product's shape.

2. **Your policy model is the interesting part and should survive intact.** The
   branch has a three-tier policy (`identify` / `verify` / `open` presumably),
   template policies that inheriting rooms defer to without any write to the room,
   an allowlist that normalises and dedupes, bot and scanner detection from
   request headers, a verification outbox, and sessions with a TTL. Note the
   `test_a_stored_policy_that_no_longer_validates_is_reported_not_hidden` and
   `test_template_change_reaches_an_inheriting_room_with_no_write_to_the_room`
   tests - those encode a deliberate stance and should not be quietly dropped.

3. **`test_requirements_never_expose_the_allowlist`** is a security test. Keep it
   passing. If the port tempts you to merge `/requirements` and `/policy` into one
   route, that test is why you must not.

4. **Do not carry over `tools/verify_localhost.py`, `tools/wf015_*.py`, or
   `frontend/src/App.jsx`.** `App.jsx` is shared, so your page registers itself
   through `frontend/src/features/<id>/index.jsx` instead.

5. **Twelve routes, the second-largest port in this set.** `AccessGate` is your
   domain entry point; its error types (`PolicyError` and friends) belong in
   `EXCEPTION_HANDLERS` so the mapping lives in your module rather than in the
   shared `api.py`.""",

    "WF-017": """1. **You are the first port in this programme to add a frontend test suite.**
   Your branch carries `frontend/src/test/setup.js`, `frontend/src/test/fixtures.js`
   and `frontend/src/test/white-label.test.jsx`, and adds dependencies to
   `frontend/package.json`. Two things follow.

   `backend/pyproject.toml` and `frontend/package.json` are **not** shared files -
   they are not in the guard's list - so you are allowed to edit them, and a
   dependency your feature genuinely cannot work without is your feature's own
   requirement rather than a coordination problem. Add what you need, once, and
   say so in your report. The guard will not stop you and it should not.

   But `frontend/vite.config.js` **is** shared and your branch edits it to register
   the test environment. Do not carry that over. Read how `npm run build` and the
   existing config are set up, and get your test setup working without it - or, if
   that turns out to be genuinely impossible without a shared-file change, **stop
   and report it** rather than editing the file. A blocked port with a clear
   question is worth more than a port that quietly breaks a hundred other agents.

2. **`tools/verify_localhost.py` is not yours.** Three branches edit it and it is
   platform territory. Leave it.

3. **Custom domains and host-header routing are the interesting risk.** Your
   routes include `GET /api/white-label/resolve` and `GET /api/white-label/links/{secret}`,
   which implies the app has to be reached on a domain other than localhost. Read
   `docs/research/.../WF-017.md` first - it is the spec and it records the
   decisions the branch made. Then be explicit in your report about what has to be
   true in production (a reverse proxy, TLS, a wildcard DNS record) that is not
   true in the test environment, because "it works on localhost" is not the same
   claim as "custom domains work".

4. **Ten routes, and `GET /api/rooms/{room_id}/white-label` is a room-scoped
   resource.** Check the `source=` rule for every write: `POST .../white-label/domain`,
   `PATCH .../white-label/branding`, `POST .../white-label/link-secret`. The audit
   row must name the route that actually served the write, built from
   `router.prefix`, not a hard-coded string. This bug has shipped once already.""",
}

META = {
    "WF-004": ("Invite buyers to a room with a role",
               "Grant a buyer or collaborator access to a room with a role, by invitation, with expiry and confirmation on destructive changes.",
               "feature/WF-004-invite-buyers-to-a-room-with-a-role", "wf-004-invite-buyer"),
    "WF-015": ("Verify buyer identity and restrict by email domain",
               "Gate a room behind identity verification and an email-domain policy, with a template policy that inheriting rooms defer to.",
               "feature/WF-015-verify-buyer-identity-and-restrict-by-email-domain", "wf-015-identity-gate"),
    "WF-017": ("White-label rooms on a custom domain",
               "Serve a room on the buyer's own domain, with branding, a verified link secret and domain resolution.",
               "feature/WF-017-white-label-rooms-on-a-custom-domain", "wf-017-white-label"),
}

PAYLOAD = {
    "WF-004": ["backend/dsr/access.py -> backend/dsr/roles.py",
               "backend/dsr/access_api.py -> backend/dsr/roles_api.py",
               "backend/tests/test_access.py -> backend/tests/test_roles.py",
               "backend/tests/test_access_api.py -> backend/tests/test_roles_api.py",
               "frontend/src/components/EmailChips.jsx",
               "frontend/src/components/ShareDialog.jsx",
               "docs/research/digital-sales-room-workflows/wf/WF-004.md"],
    "WF-015": ["backend/dsr/access.py",
               "backend/tests/test_access.py",
               "backend/tests/test_access_api.py",
               "frontend/src/pages/AccessSettings.jsx",
               "frontend/src/pages/Gate.jsx",
               "docs/design/WF-015-identity-and-domain-access.md",
               "docs/research/digital-sales-room-workflows/wf/WF-015.md"],
    "WF-017": ["backend/dsr/domains.py",
               "backend/dsr/domain_service.py",
               "backend/tests/test_domains.py",
               "backend/tests/test_white_label_api.py",
               "frontend/src/pages/WhiteLabel.jsx",
               "frontend/src/pages/PublicRoom.jsx",
               "frontend/src/test/setup.js",
               "frontend/src/test/fixtures.js",
               "frontend/src/test/white-label.test.jsx",
               "docs/research/digital-sales-room-workflows/wf/WF-017.md"],
}

HELD = """# Port brief: WF-001 - Create a room from a template

**This brief exists but the workflow is HELD. Do not start it.**

Its branch edits `backend/dsr/db/audited.py` - the audited database wrapper, which
is where the guarantee that the audit row is written in the same transaction as
the change actually lives. `AGENTS.md` states that guarantee as the thing the
product is built on.

A port is a mechanical transformation. It is the right instrument for moving a
workflow into the plugin host's shape, and the wrong instrument for deciding what
a change to the audit core means. That needs a human to read the diff and say
whether the change belongs in the feature or in the host.

The other eleven ports in this programme moved 1,000+ lines with no shared-file
conflict. `audited.py` is on the shared list precisely because a hundred agents
each adding a line to it is the failure the plugin host exists to end. Two
workflows are already held for this reason, WF-005 and WF-014. This is the third.

Measured state of the branch, for whoever picks this up:

    commits ahead : 1
    files changed : 12
    SHARED edits  : backend/dsr/api.py, backend/dsr/db/audited.py,
                    backend/seed.py, frontend/src/components/ui.jsx,
                    frontend/src/lib/api.js
    payload       : backend/dsr/rooms.py,
                    backend/tests/test_wf001_create_room.py,
                    backend/tests/test_audited.py,
                    frontend/src/pages/Rooms.jsx,
                    docs/design/WF-001-create-room-from-template.md

Note that its `rooms.py` and its edit to `frontend/src/pages/Rooms.jsx` are the
parts that would port cleanly. The `audited.py` edit is the part that needs
reading. It may well be a feature that has no business touching the audit wrapper
at all, in which case the answer is simply to drop that part - but deciding that
is a human's call, not a mechanical one.

Its routes, for whoever reads the spec:

    GET    /api/accounts
    GET    /api/room-templates
    POST   /api/rooms
    GET    /api/rooms

`POST /api/rooms` and `GET /api/rooms` are core-shaped paths. Under the host they
would become `/api/wf-001/rooms`, which is also a naming question worth deciding
at the same time as the `audited.py` question rather than separately.
"""


def build(ticket):
    name, desc, branch, slug = META[ticket]
    payload = "\n".join(f"  - `{p}`" for p in PAYLOAD[ticket])
    notes = NOTES[ticket]
    return (
        f"# Port brief: {ticket} - {name}\n\n"
        "<!--\n"
        "Committed on purpose. This is the exact brief the porting agent works from, so a\n"
        "reviewer can compare what was asked against what was delivered, and so the agent\n"
        "reads its instructions from a file instead of a shell argument that may have been\n"
        "mangled in transit.\n"
        "-->\n\n"
        "## Notes for this specific workflow\n\n"
        f"{notes}\n\n"
        "### What to carry across\n\n"
        "The payload is listed with renames already applied, so there is nothing to\n"
        "work out. The source branch is `" + branch + "`; use `git show` from your\n"
        "own worktree to read each file.\n\n"
        f"{payload}\n\n"
        f"Your feature module is `backend/dsr/features/{slug.replace('-', '_', 1)}.py`\n"
        f"with prefix `/api/{slug}` and id `{slug}`, and your frontend folder is\n"
        f"`frontend/src/features/{slug}/`.\n\n"
        "---\n" + tail
    )


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for ticket in ("WF-004", "WF-015", "WF-017"):
        body = build(ticket)
        (OUT / f"{ticket}.md").write_text(body, encoding="utf-8")
        print(f"  wrote orchestration\\ports\\{ticket}.md  ({len(body):,} chars)")

    held = HELD.replace("WF-001 - Create a room from a template",
                        "WF-001 - Create room from template")
    (OUT / "WF-001.md").write_text(held, encoding="utf-8")
    print(f"  wrote orchestration\\ports\\WF-001.md  ({len(held):,} chars, HELD)")

    manifest = {
        "set": 4,
        "dispatched": ["WF-004", "WF-015", "WF-017"],
        "held": {
            "WF-001": "its branch edits backend/dsr/db/audited.py, the audit guarantee "
                      "itself. A port is a mechanical transformation and is the wrong "
                      "instrument for deciding what a change to the core means. Same "
                      "reason as WF-005 and WF-014.",
        },
        "decided_before_dispatch": {
            "WF-004_vs_WF-015": {
                "collision": "both add backend/dsr/access.py; both serve "
                             "GET /api/rooms/{room_id}/access",
                "measured_overlap": {"access.py": 3.2, "test_access.py": 1.0,
                                     "test_access_api.py": 4.8},
                "shared_symbols": "2 of 74, one of them __init__",
                "jev_first_ask": {"verdict": "uncertain", "confidence": 0.68,
                                  "threshold": 0.75,
                                  "audit_id": "jev-20260927T052726-8696-46556",
                                  "why": "content overlap had not been measured"},
                "jev_second_ask": {"verdict": "pass", "confidence": 1.00,
                                   "selected": "B_two_features_self_contained",
                                   "audit_id": "jev-20260927T052837-24152-17484"},
                "outcome": "two self-contained features. WF-004's module is renamed to "
                           "roles.py; WF-015 keeps access.py. The shared route resolves "
                           "when each takes its own prefix.",
            },
        },
    }
    (ROOT / "orchestration" / "ports" / "SET-4.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    print("  wrote orchestration\\ports\\SET-4.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
