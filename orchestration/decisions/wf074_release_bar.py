"""WF-074: the release bar for a finished, tested change.

The pinned question set from ADR-0003, so this landing is comparable with every other one. It is
asked about the finished change and not about a design document, because the two answer different
questions and a gate on a document is not evidence about the code.

Every number below was measured, and the commands that produced each one are named in the state so
a reviewer can re-run them rather than trust the figure. Where a measurement was taken with other
agents on the same machine, that is declared rather than left for the reader to guess.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

os.environ["JEV_AUDIT_LOG"] = str(ROOT / "orchestration" / "decisions" / "wf074-jev-audit.jsonl")

from jev import Jev  # noqa: E402

CHANGE_SUMMARY = """
WF-074: scope visibility to a named audience with per-item permissions. A feature plugin owning
five paths and no shared file.

Domain package backend/dsr/audience_permissions/. vocabulary.py holds every researched term with
its quote, rules.py holds the pure decisions and takes no database, inferences.py holds the nine
recorded derivations with the alternative each rejected, and engine.py holds the only reads and
writes. Feature module backend/dsr/features/wf074_scope_visibility_to_an_audience_with_per_item.py
exports FEATURE, a router on /api/wf-074, EXCEPTION_HANDLERS and seed(db, context). Tests
backend/tests/test_wf074.py (122 tests) and test_wf074_http.py (71 tests, 6 skipped because they
need frontend sources). Frontend frontend/src/features/wf-074-scope-visibility-to-an-audience-with-per-item/
with index.jsx, api.js, primitives.jsx, icons.js and 34 tests.

The sourced rules enforced, each with its own tests: the default is deny and an absent row is not
a wildcard; nobody-granted-it and somebody-revoked-it are two different states; the two flags are
independent and the entry is closed; ancestors of a made-visible item are opened as a write in the
same transaction; membership is explicit email then domain then allow_all with allow_all
short-circuiting the two before it; adding a member is idempotent and sends nothing; domains
normalise from both spellings with duplicates removed; the group ACL is a delta upsert and the
link ACL is a full replace and they share no implementation; a group link refuses a link override
with 422 and writes nothing; a general link that was never scoped and one whose scope was cleared
are distinguished by a derived marker; revocation needs no re-share; three size caps are enforced
where the list is built; the room's library rows are read by the envelope room_id and never written,
and a dangling grant is reported rather than dropped.

Two defects were found by writing the tests rather than by reading the code. The ancestor walk was
resolving every document to the room root, because the parent map held only folders while the walk
reads the item's own parent from the same map, so no document ever opened a folder and the rule
silently did nothing. And a view reported a scoped link as cleared, because the scope state was
computed from a literal zero rather than the link's live row count.

One route was changed by the HTTP tests: the member call took a dict body, so a caller holding a
bare array of addresses got a 422 from FastAPI's validator before the engine saw it.
"""

MEASUREMENTS = {
    "tests_added_backend_domain": 122,
    "tests_added_backend_http": 71,
    "tests_added_frontend": 34,
    "own_files_domain_alone": "122 passed in 0.61s",
    "own_files_http_alone": "71 passed, 6 skipped in 3.53s (the 6 need frontend sources, which are present, so they run in the full suite)",
    "whole_backend_suite": "15390 passed, 1 xfailed in 416.54s under pytest-xdist",
    "whole_backend_suite_caveat": (
        "Measured with three other agents running on the same eight cores. The brief measures the "
        "same run of white-label.test.jsx failing 9 of 42 under that contention and 1 of 42 alone. "
        "CI is the authority."
    ),
    "backend_coverage_total": "95% (gate is 90%)",
    "backend_coverage_this_change": {
        "dsr/audience_permissions/engine.py": "98%",
        "dsr/audience_permissions/rules.py": "95%",
        "dsr/features/wf074_scope_visibility_to_an_audience_with_per_item.py": "99%",
    },
    "frontend_suite": "785 passed across 29 files",
    "frontend_build": "vite build succeeded, 390 modules transformed",
    "frontend_lint": "0 errors, 12 warnings, none in this feature's own files",
    "frontend_format_check": "All matched files use Prettier code style",
    "ruff_check": "All checks passed, from the repository root with --config backend/pyproject.toml",
    "ruff_format_check": "772 files already formatted, 0 would be reformatted",
    "routes_called_live": (
        "1425 routes across 77 features called against uvicorn on 127.0.0.1:8123 over a freshly "
        "seeded database: 639x200, 13x201, 173x400, 1x401, 42x403, 452x404, 14x409, 91x422, and "
        "zero 5xx"
    ),
    "this_features_own_routes": "20 routes mounted, 0 failed features, checked over live HTTP",
    "seed": (
        "backend/seed.py exits 0 and prints: 3 audiences (3 member rows, 1 open to anyone, 2 "
        "granted nothing yet); 3 group permission rows across 8 items in the room, 1 dangling; "
        "3 links (1 group-scoped, 2 general) in states group, scoped, unscoped; 1 item granted on "
        "the scoped general link; 1 member revoked with no re-share. The whole line is ASCII and "
        "encodes under cp1252, asserted in the test suite."
    ),
    "live_behaviour_walked_by_hand": (
        "Against uvicorn on 127.0.0.1:8125: a member sees 2 of 9 items on a group link and a "
        "stranger is refused with not_a_member; a link override on the group link answers 422 "
        "group_link_rejects_link_overrides; a closed entry answers 400 keyed permissions[0]; a bad "
        "domain answers 400 keyed domains; a bare address list answers 201; every audit source on "
        "this feature's permission rows is the string seed or a route the host mounted"
    ),
    "shared_files_edited": 0,
    "guard_result": "tools/check_feature_diff.py on the 146 changed files: OK, none shared",
    "no_shared_file_in_diff": (
        "backend/dsr/api.py, deps.py, store.py, db/audited.py, seed.py, App.jsx, main.jsx, "
        "lib/api.js, lib/features.js, components/ui.jsx, vite.config.js are all absent from the diff"
    ),
    "primitives_built_locally": (
        "Notice, Select, FlagToggle, PermissionGrid, StateBadge and ScopeState are rebuilt in this "
        "feature's own primitives.jsx because the shipped components/ui.jsx lacks Notice and Toggle "
        "and has no select or checkbox. components/ui.jsx is not edited. This is the contract's "
        "instruction for this case and it is promotion work for the integrator, not a workaround."
    ),
    "jev_design_gate": (
        "pass at confidence 1.00, audit jev-20261005T051919-25896-59873, for the scope marker. "
        "An earlier question came back uncertain at 0.58 (jev-20261005T051741-9920-61850) and was "
        "narrowed with the vendor CLI page fetched as evidence rather than re-asked."
    ),
}

UNVERIFIED = (
    "No browser rendering pass was run, so the page has not been seen rendered. No screen-reader "
    "pass, so the label wiring is asserted from the DOM rather than from how it is announced. One "
    "browser engine only. Not verified at mobile widths or with prefers-reduced-motion set. The "
    "frontend suite, the build and the design floor were checked on this machine under contention, "
    "not on a clean runner."
)


def main() -> int:
    client = Jev()
    record = client.release_bar(
        ticket="WF-074",
        change_summary=CHANGE_SUMMARY,
        measurements=MEASUREMENTS,
        unverified=(UNVERIFIED,),
    )
    print(record.summary())
    return 0 if record.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
