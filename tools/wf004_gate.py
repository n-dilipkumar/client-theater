#!/usr/bin/env python3
"""Implementation gate for WF-004, run by the implementation sub-agent.

    ./.venv/Scripts/python tools/wf004_gate.py

Records the merge-readiness judgment in the shared audit log. This is the
sub-agent's own gate; the Orchestrator runs the merge gate after review.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jev import Jev  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def suite() -> str:
    # addopts in pyproject.toml already sets -q, so passing -q again doubles it
    # to -qq and suppresses the summary line. -v keeps it.
    result = subprocess.run(
        [
            str(ROOT / ".venv/Scripts/python.exe"),
            "-m",
            "pytest",
            "-v",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
    )
    for line in (result.stdout + result.stderr).splitlines():
        if " passed" in line or " failed" in line or " error" in line:
            return line.strip()
    return "no summary line found"


CHANGES = """New files
- backend/dsr/access.py          WF-004 domain: role vocabulary, delegation rule,
                                 48h invitation window, end-of-day-UTC expiry,
                                 imminent-expiry banner, AccessService over RecordStore
- backend/dsr/access_api.py      HTTP router for the Share dialog and Who Has Access
- backend/tests/test_access.py   82 tests: the rules, with an injected clock
- backend/tests/test_access_api.py 45 tests: status codes and envelope
- frontend/src/components/EmailChips.jsx  Enter/comma commits an address to a chip
- frontend/src/components/ShareDialog.jsx Share dialog: invite form + Who Has Access
- docs/wf-004.md                 sourced behaviours vs design inferences
- tools/wf004_decide.py          the recorded two_collections architecture decision
- tools/wf004_smoke.py           end-to-end HTTP check against a running server

Modified
- backend/dsr/api.py   AccessError handler (403/404/400/428); router included before
                       the SPA catch-all so the routes are not shadowed
- backend/seed.py      demo access grants across all three expiry outcomes, one
                       pending invitation, and a missing-mkdir fix
- frontend/src/components/ui.jsx  9 SVG icons, a focus-managed Modal, a Notice
- frontend/src/lib/api.js        accessApi client; errors now carry HTTP status
- frontend/src/pages/Rooms.jsx   Share action per room, gated on the server's
                       can_share; a "Viewing as" identity control
"""


def state() -> str:
    return (
        "Invite buyers to a room with a role and an access expiry. "
        "Two schema-flexible collections (room_invitation, room_access), both stored "
        "as arbitrary JSON in records.data with no migration and no typed column; "
        "every write goes through AuditedDatabase so every change is audited in the "
        "same transaction. The 48-hour acceptance window, the end-of-expiration-date "
        "in UTC cut-off, the seven-day imminent-expiry banner, the role delegation "
        "rule, and the immutable Owner are all enforced on the server, not just the "
        "UI. React + Tailwind Share dialog meeting the design system's 44px targets, "
        "visible focus, SVG icons with no emoji, and reduced-motion handling.\n\n"
        + CHANGES
    )


if __name__ == "__main__":
    client = Jev()

    # First run recorded a "fail" here even though Jev chose `merge` at 0.91.
    # The cause was in tools/jev.py: every high-level helper passes
    # pass_option="verdict", the gate question's NAME, while the gate compared
    # that against the chosen CRITERIA key ("merge"). The two could never match,
    # so no choice gate could ever pass. That is fixed and unit-tested in
    # tools/test_jev_gate.py; this is the ordinary helper call again.
    record = client.score_implementation(ticket="WF-004", diff_summary=state())

    print(record.summary())
    print()
    print(f"pytest: {suite()}")
    sys.exit(0 if record.passed else 1)
