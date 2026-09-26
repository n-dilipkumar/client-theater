## What this changes

<!-- One or two sentences. What a reviewer needs to know to understand the diff. -->

Closes <!-- WF-0xx, or "part of set N" -->

## Is this a feature or platform work?

- [ ] **Feature** — adds `backend/dsr/features/<ticket>_<slug>.py` and
      `frontend/src/features/<id>/index.jsx`, and edits no shared file.
- [ ] **Platform** — deliberately changes a shared file. Say which and why below.

A PR that is a feature and also edits a shared file will fail the
`Feature contract` CI job. That is not a bug to work around: the host exists
precisely so features register by adding files, and a shared-file edit means
every other feature branch will conflict at the same line.

If you genuinely need a new extension point, that is platform work and belongs
in its own PR, separately reviewable — not bundled into a feature.

## Shared-file check

- [ ] `python tools/check_feature_diff.py --base origin/main` reports
      `OK: ... none shared`

## Evidence

Do not assert that it works. Paste what you ran.

- [ ] Backend suite green: `cd backend && ../.venv/Scripts/python -m pytest`
      <!-- test count before -> after -->
- [ ] Frontend build clean: `cd frontend && npm run build`
- [ ] Local host app verified: `.venv/Scripts/python tools/verify_localhost.py`
      <!-- check count -->
- [ ] The feature is reachable over HTTP, and shows in `/api/features` as
      `loaded: true` with its routes listed.
- [ ] The page has data in the demo, contributed by this feature's
      `seed(db, context)` hook rather than by editing `backend/seed.py`.

## Storage and audit

- [ ] Every read and write goes through `StoreDep` / `RecordStore`. No direct
      SQLite connection was added.
- [ ] Audit rows name the route that actually served the write. If a domain
      function hardcodes a URL for `source=`, that is a defect — the HTTP layer
      passes `source` in.
- [ ] Payloads stay schema-flexible. No migration, no typed column, no new
      required field. A team adding a field must not need coordination.

## Design

- [ ] Follows `design-system/digital-sales-room/MASTER.md`: 44px touch targets,
      visible focus rings, 4.5:1 text contrast, `prefers-reduced-motion`
      respected, no emoji as icons.
- [ ] Uses shared primitives from `components/ui.jsx` rather than shipping
      another `Modal`. Anything genuinely feature-specific lives in the feature
      folder and is called out below.
- [ ] Icon: reused a name from the existing set, or passed `iconPath` /
      `<Icon path=...>`. Did **not** add to the shared `PATHS` map.

## Research provenance

- [ ] This workflow is specified in
      `docs/research/digital-sales-room-workflows/wf/<WF-0xx>.md`, and the
      implementation matches that spec rather than a guess.
- [ ] Where the research could not be sourced, the doc says so and the
      implementation labels the behaviour as a hypothesis. An unsourced
      workflow is a hypothesis, not a specification.

## Known limitations

<!-- Anything a reviewer should be suspicious of. "None" is a valid answer. -->
