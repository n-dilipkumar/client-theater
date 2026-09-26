"""Generate the committed port brief for each workflow in a dispatch set.

The brief is committed rather than passed inline for two reasons. Shell quoting
for a multi-kilobyte prompt sent to a terminal is fragile, and an agent that
cannot read its own instructions cannot be held to them. Committing it means the
exact brief each agent worked from is in git, so a reviewer can see what was
asked and compare it against what was delivered.

Run from the repo root:  .venv/Scripts/python.exe orchestration/port_prompt.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestration"))

from port_prompt import build  # noqa: E402

SHARED = [
    "backend/dsr/api.py",
    "backend/dsr/deps.py",
    "backend/dsr/store.py",
    "backend/dsr/db/audited.py",
    "backend/seed.py",
    "frontend/src/App.jsx",
    "frontend/src/main.jsx",
    "frontend/src/lib/api.js",
    "frontend/src/lib/features.js",
    "frontend/src/components/ui.jsx",
    "frontend/vite.config.js",
]

# Dispatch set 1. Held back on purpose:
#
#   WF-005  its branch edits db/audited.py and store.py - the audit guarantee
#           itself. Needs a human read of what it changed before anything carries
#           it across, not an agent port.
#   WF-007  collides with WF-010 on /api/library. Needs a scope decision.
#   WF-009  collides with WF-011 on the publishing router. Needs a scope decision.
#   WF-010  collides with WF-007. Needs a scope decision.
#   WF-011  collides with WF-009. Needs a scope decision.
#   WF-001, WF-004, WF-015, WF-017
#           rescued this session from uncommitted working trees. Untrusted input
#           until read: WF-004 also modified tools/jev.py, the validator every
#           decision in this project goes through.
TICKETS = [
    {
        "ticket": "WF-002",
        "source_branch": "feature/WF-002-build-the-room-s-buyer-facing-pages-from",
        "feature_module": "wf002_pages.py",
        "feature_id": "wf-002-buyer-pages",
        "prefix": "wf-002",
        "name": "Build the room's buyer-facing pages from fragments",
        "description": "Assemble a buyer-facing page from reusable fragments and render it in the room.",
        "additive": [
            "backend/dsr/fragments.py",
            "backend/dsr/pages.py",
            "backend/dsr/routes_pages.py",
            "backend/tests/test_pages.py",
            "frontend/src/components/BlockRenderer.jsx",
            "frontend/src/components/ConfigPanel.jsx",
            "frontend/src/pages/PageBuilder.jsx",
            "frontend/src/pages/RoomView.jsx",
        ],
        "notes": [
            "The branch ships a separate `routes_pages.py` holding an `APIRouter`. "
            "Fold those routes into the feature module's own `router` rather than "
            "keeping two routers, so there is one prefix to reason about and one "
            "place the host mounts.",
            "`BlockRenderer.jsx` and `ConfigPanel.jsx` are genuinely specific to "
            "this feature, so they belong in the feature folder, not in the shared "
            "`components/` directory where the branch put them.",
            "The branch modified `frontend/src/pages/Rooms.jsx`, a core page. Do not "
            "carry that over. If the page builder genuinely needs a link or button "
            "on the core Rooms page, that is a shared-file change a human must "
            "decide on - report it rather than making it.",
        ],
    },
    {
        "ticket": "WF-003",
        "source_branch": "feature/WF-003-populate-and-govern-the-room-s-document-library",
        "feature_module": "wf003_library.py",
        "feature_id": "wf-003-document-library",
        "prefix": "wf-003",
        "name": "Populate and govern the room's document library",
        "description": "Govern the room's document library: what is in it, and who may see each document.",
        "additive": [
            "backend/dsr/documents.py",
            "backend/dsr/permissions.py",
            "backend/tests/test_documents.py",
            "backend/tests/test_documents_api.py",
            "backend/tests/test_permissions.py",
            "frontend/src/pages/Documents.jsx",
        ],
        "notes": [
            "`permissions.py` looks like something the whole product would want, and "
            "it may well end up shared. Keep it inside the feature module for now. "
            "If you conclude another feature needs it, say so in your report and a "
            "human can promote it deliberately, once, instead of two features each "
            "carrying a copy.",
            "`permissions.py` likely defines the domain error types this feature maps "
            "to HTTP. Those go in `EXCEPTION_HANDLERS`, which is how a feature "
            "describes its error mapping without editing `api.py`.",
            "The branch edited `backend/seed.py` purely to add library demo rows. Move "
            "that into this module's `seed(db, context)` instead - that is exactly "
            "what the hook exists for.",
        ],
    },
    {
        "ticket": "WF-012",
        "source_branch": "feature/WF-012-generate-a-personalised-room-programmatically-from-a-template",
        "feature_module": "wf012_generation.py",
        "feature_id": "wf-012-room-generation",
        "prefix": "wf-012",
        "name": "Generate a personalised room programmatically from a template",
        "description": "Generate a personalised room programmatically from a template.",
        "additive": [
            "backend/dsr/generation.py",
            "backend/tests/test_generation.py",
            "frontend/src/pages/Generator.jsx",
        ],
        "notes": [
            "The smallest port in the set: three payload files and no exception "
            "handlers. If this one goes wrong, the cause is worth reading carefully "
            "before the larger ports are trusted.",
            "This branch's work was rescued from an uncommitted working tree in a "
            "previous session and had never been run. Treat the code as an untested "
            "starting point to read, not as work that passed anything. Its tests in "
            "particular have probably never been executed.",
        ],
    },
]


def main():
    out_dir = ROOT / "orchestration" / "ports"
    out_dir.mkdir(parents=True, exist_ok=True)

    for spec in TICKETS:
        prompt = build(
            ticket=spec["ticket"],
            source_branch=spec["source_branch"],
            feature_module=spec["feature_module"],
            feature_id=spec["feature_id"],
            prefix=spec["prefix"],
            additive=spec["additive"],
            shared=SHARED,
            name=spec["name"],
            description=spec["description"],
        )

        notes = "\n".join(f"{i}. {n}" for i, n in enumerate(spec["notes"], 1))
        body = f"""# Port brief: {spec["ticket"]} - {spec["name"]}

<!--
Committed on purpose. This is the exact brief the porting agent works from, so a
reviewer can compare what was asked against what was delivered, and so the agent
reads its instructions from a file instead of a shell argument that may have been
mangled in transit.
-->

## Notes for this specific workflow

{notes}

---

{prompt}
"""
        path = out_dir / f"{spec['ticket']}.md"
        path.write_text(body, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}  ({len(body):,} chars)")

    print(f"\n{len(TICKETS)} briefs in {out_dir.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
