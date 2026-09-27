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

# Dispatch set 3. WF-008, WF-013, WF-016.
#
# Held back on purpose, still:
#
#   WF-005, WF-014
#           both branches edit db/audited.py AND store.py - the audit guarantee
#           itself, written by a dozen different agents. A port is the wrong
#           instrument: something has to read what they changed to the core and
#           decide whether the change belongs in the feature or in the host. That
#           is a human judgement about the product's central promise.
#   WF-001, WF-004, WF-015, WF-017
#           rescued this session from uncommitted working trees. Untrusted input
#           until read: WF-004 also modified tools/jev.py, the validator every
#           decision in this project goes through.
#
# WF-008 was measured against WF-007 and WF-010 before being added here, because
# all three use /api/library and PORT-PLAN.md was wrong once for comparing
# prefixes. Measured: 0 colliding (method, path) pairs in all three pairings.
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
    {
        "ticket": "WF-007",
        "source_branch": "feature/WF-007-ingest-a-document-or-deck-into-the-content",
        "feature_module": "wf007_library.py",
        "feature_id": "wf-007-content-library",
        "prefix": "library",
        "name": "Ingest a document or deck into the content library",
        "description": "Ingest a document or deck into the room's content library: folders, uploads, and thumbnails.",
        "additive": [
            "backend/dsr/library.py",
            "backend/dsr/library_api.py",
            "backend/tests/test_library.py",
            "frontend/src/pages/Library.jsx",
        ],
        "notes": [
            "This workflow was held back believing it collided with WF-010 on "
            "/api/library. It does not. WF-007 owns /api/library/rooms/{id}/documents, "
            "/api/library/documents/{id} and its content and thumbnail sub-paths; WF-010 "
            "owns /api/library/contract, /fields, /search, /assemble and /searches. Zero "
            "concrete paths overlap, and the host allows two features to share a prefix. "
            "You may safely use the prefix /api/library, which keeps this feature's public "
            "URL identical to the branch's.",
            "The branch also added `orchestration/decisions/wf-007-*.json` and three "
            "`tools/jev*.py` files, and modified `tools/jev.py` and "
            "`tools/verify_localhost.py`. Do NOT carry any of those over. `tools/` and "
            "`orchestration/` are platform territory, and another agent may be changing "
            "them at the same time.",
            "The branch modified `backend/pyproject.toml` and `.gitignore`. Leave both "
            "alone. If this feature genuinely needs a new dependency, say so in your "
            "report and let a human add it once, deliberately - not four agents each "
            "adding their own.",
        ],
    },
    {
        "ticket": "WF-010",
        "source_branch": "feature/WF-010-search-the-content-library-to-assemble-a-room",
        "feature_module": "wf010_library_search.py",
        "feature_id": "wf-010-library-search",
        "prefix": "library",
        "name": "Search the content library to assemble a room",
        "description": "Search the content library and assemble a room from what the search returns.",
        "additive": [
            "backend/tests/test_documents.py",
            "backend/tests/test_documents_api.py",
            "backend/tests/test_permissions.py",
        ],
        "notes": [
            "IMPORTANT: the file list above is incomplete and you must derive the real one. "
            "WF-010 registered its routes directly on the shared `app` object rather than "
            "behind an `APIRouter`, so `git diff --name-only origin/main...<branch>` does not "
            "show a module holding them. Run that diff yourself and read the api.py delta to "
            "find the module that actually contains LibrarySearch, LibraryAssembler, "
            "LibrarySchema, LibraryHit, Cursor, Group, Match, FieldText and Condition. Those "
            "symbols are the library-search domain and they are what you are porting.",
            "This workflow was held back believing it collided with WF-007 on "
            "/api/library. It does not: you own /api/library/contract, /fields, /search, "
            "/assemble and /searches, while WF-007 owns the /rooms/{id}/documents and "
            "/documents/{id} paths. Zero concrete paths overlap. Use the prefix /api/library.",
            "Two features sharing the prefix /api/library is the case the plugin host was "
            "built for, but it is only safe because your concrete paths differ. Before you "
            "finish, confirm your mounted paths contain no /rooms/{room_id}/documents or "
            "/documents/{document_id} path, which is WF-007's.",
        ],
    },
    {
        "ticket": "WF-009",
        "source_branch": "feature/WF-009-approve-and-publish-library-content-immediately-or-on",
        "feature_module": "wf009_publishing.py",
        "feature_id": "wf-009-publishing",
        "prefix": "publishing",
        "name": "Approve and publish library content, immediately or on schedule",
        "description": "Approve library content through a workflow and release it, immediately or on a schedule.",
        "additive": [
            "backend/dsr/publishing.py",
            "backend/dsr/publishing_api.py",
            "backend/tests/test_wf009_publishing.py",
            "frontend/src/pages/Publishing.jsx",
        ],
        "notes": [
            "This workflow was held back believing it collided with WF-011 on a publishing "
            "router. It does not. You own /api/publishing/processes, /submissions, /workflows "
            "and its steps, /publish, /publications, /publications/due, /folders and "
            "/subscriptions. WF-011 owns /api/publishing/rooms and its status, share-link, "
            "access, /events and /webhooks paths. Zero concrete paths overlap, and the host "
            "allows a shared prefix. Use the prefix /api/publishing.",
            "The branch modified `backend/dsr/store.py`, which is a shared file. Do NOT carry "
            "that over. If your feature needs something store.py does not provide, report it as "
            "a finding rather than editing it - CI will fail the branch and the right fix is a "
            "deliberate platform change.",
            "Your module defines a class named `PublishingService`, and so does WF-011's. That "
            "is fine and expected: your port becomes backend/dsr/features/wf009_publishing.py "
            "and theirs becomes wf011_publishing.py, so they are separate files that never "
            "collide. Do not try to share one service with WF-011 - neither of you can see the "
            "other's branch, and a shared module is precisely what the plugin host removes.",
        ],
    },
    {
        "ticket": "WF-011",
        "source_branch": "feature/WF-011-take-a-room-from-draft-to-live-and",
        "feature_module": "wf011_publishing.py",
        "feature_id": "wf-011-room-handover",
        "prefix": "publishing",
        "name": "Take a room from draft to live and hand over the link",
        "description": "Take a room from draft to live, then hand over the link and manage access after handover.",
        "additive": [
            "backend/dsr/publishing.py",
            "backend/dsr/routes_publishing.py",
            "backend/tests/test_api_publishing.py",
            "backend/tests/test_publishing.py",
            "frontend/src/lib/publish.js",
            "frontend/src/pages/Publish.jsx",
        ],
        "notes": [
            "This workflow was held back believing it collided with WF-009 on a publishing "
            "router. It does not. You own /api/publishing/rooms and /rooms/{id}/status, "
            "/share-link, /access, /events, /webhooks and /webhooks/{id}/deliveries. WF-009 "
            "owns /processes, /submissions, /workflows, /publish, /publications, /folders and "
            "/subscriptions. Zero concrete paths overlap. Use the prefix /api/publishing.",
            "Your module also defines a class named `PublishingService`, independently written "
            "from WF-009's with different operations: yours is room lifecycle and handover "
            "(room, status_of, public_url, share, board, set_status, set_access, subscribe, "
            "cancel_subscription, events, deliveries), theirs is approval and release "
            "(create_process, submit, decide, publish, run_due). Keep yours as-is. Your port "
            "becomes backend/dsr/features/wf011_publishing.py and theirs becomes "
            "wf009_publishing.py, so the shared class name is harmless. Attempting to merge "
            "the two would require editing a file you cannot see.",
            "`frontend/src/lib/publish.js` is an API wrapper. Move it inside your feature "
            "folder as `api.js` and have it call `apiRequest` from `@/lib/api` - do NOT add "
            "methods to the shared `frontend/src/lib/api.js`, which is a shared file.",
        ],
    },
    {
        "ticket": "WF-008",
        "source_branch": "feature/WF-008-auto-sync-an-external-cloud-file-into-the",
        "feature_module": "wf008_external_sync.py",
        "feature_id": "wf-008-external-sync",
        "prefix": "library",
        "name": "Auto-sync an external cloud file into the content library",
        "description": "Keep an external cloud file in sync with the content library, on a connection the team configures.",
        "additive": [
            "backend/dsr/library/__init__.py",
            "backend/dsr/library/errors.py",
            "backend/dsr/library/ratelimit.py",
            "backend/dsr/library/sources.py",
            "backend/dsr/library/sync.py",
            "backend/dsr/library_api.py",
            "backend/tests/test_library_api.py",
            "backend/tests/test_library_sync.py",
            "frontend/src/pages/ExternalSync.jsx",
        ],
        "notes": [
            "THREE features now share the /api/library prefix: you, WF-007 and WF-010. That is "
            "safe, and it was measured rather than assumed - all three pairings have 0 "
            "colliding (method, path) pairs. Your paths are /connections, /external, "
            "/external/resync, /external/{content_id}, /external/{content_id}/sync-status, "
            "/folders and /sources. WF-007 owns /rooms/{room_id}/documents, "
            "/rooms/{room_id}/folders, /rooms/{room_id}/usage and /documents/{document_id}. "
            "WF-010 owns /contract, /fields, /search, /assemble and /searches. Use the prefix "
            "/api/library and stay inside your own paths.",
            "You and WF-007 both add a library domain package. If you find yourself wanting "
            "WF-007's ContentLibrary, do not import it and do not edit its module - you cannot "
            "see its branch, and a cross-feature import is exactly the coupling the plugin host "
            "removes. Keep yours self-contained and say so in your report if you think the two "
            "should share a domain module later; that is a human decision about the product's "
            "shape, not something to settle by reaching across a worktree.",
            "The branch also modified `tools/verify_localhost.py`. Do NOT carry that over - "
            "`tools/` is platform territory and another agent may be changing it right now. "
            "Do carry your routes across, though: the two test files above are the payload.",
        ],
    },
    {
        "ticket": "WF-013",
        "source_branch": "feature/WF-013-personalise-room-content-with-conditional-rules",
        "feature_module": "wf013_rules.py",
        "feature_id": "wf-013-conditional-rules",
        "prefix": "wf-013",
        "name": "Personalise room content with conditional rules",
        "description": "Personalise what a buyer sees in a room with rules evaluated against the buyer's context.",
        "additive": [
            "backend/dsr/rules.py",
            "backend/dsr/rules_api.py",
            "backend/tests/test_rules.py",
            "backend/tests/test_rules_api.py",
            "docs/research/digital-sales-room-workflows/wf/WF-013-design.md",
            "docs/research/digital-sales-room-workflows/wf/WF-013.md",
            "frontend/src/pages/ConditionalRules.jsx",
        ],
        "notes": [
            "This is a good-sized port: a domain module, a separate API module, and two test "
            "files. The two `rules_api` routes families are likely the domain error types this "
            "feature maps to HTTP - those belong in `EXCEPTION_HANDLERS`, which is how a "
            "feature describes its error mapping without editing `api.py`.",
            "The two research docs under docs/research/ are the spec. Read WF-013-design.md "
            "before writing the port: it records the decisions the branch made, and the point of "
            "a port is to land what was researched, not to redesign it.",
            "The branch's two test files assume routes on the shared app. They will need "
            "rewiring to your feature's own router - check them against the contract rather "
            "than assuming they pass as written.",
        ],
    },
    {
        "ticket": "WF-016",
        "source_branch": "feature/WF-016-sync-room-events-to-the-crm-via-webhooks",
        "feature_module": "wf016_crm_sync.py",
        "feature_id": "wf-016-crm-sync",
        "prefix": "wf-016",
        "name": "Sync room events to the CRM via webhooks and automations",
        "description": "Sync room events to a CRM through webhooks, deliveries and automations.",
        "additive": [
            "backend/dsr/crm/__init__.py",
            "backend/dsr/crm/automations.py",
            "backend/dsr/crm/delivery.py",
            "backend/dsr/crm/errors.py",
            "backend/dsr/crm/subscriptions.py",
            "backend/dsr/crm/sync.py",
            "backend/dsr/crm/vocabulary.py",
            "backend/tests/test_crm.py",
            "backend/tests/test_crm_api.py",
            "frontend/src/pages/CrmSync.jsx",
        ],
        "notes": [
            "A seven-module crm package is the largest payload in this set. Copy it whole, then "
            "read it before you wire it up - `vocabulary.py` and `delivery.py` in particular may "
            "be doing work the feature module should be doing.",
            "`crm/errors.py` almost certainly holds the domain error types for this feature. "
            "Those go in `EXCEPTION_HANDLERS` so the mapping lives in your module rather than in "
            "the shared `api.py`.",
            "Webhooks mean retry and failure states. Whatever the branch did for delivery "
            "retries, make sure it is a record rather than in-memory state, so it survives a "
            "restart and every attempt is auditable. If it is in-memory, that is a finding worth "
            "reporting even if you fix it.",
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
