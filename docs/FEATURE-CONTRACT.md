# Feature contract

How to add a workflow to the Digital Sales Room. Read this before you write any
code. It exists because this product is built by many agents in parallel: the
rules below are what stop a hundred features from colliding with each other.

## The one rule that matters

**You add files. You do not edit shared files.**

A feature is a new backend module and a new frontend folder. Nothing else. If
you find yourself editing `backend/dsr/api.py`, `frontend/src/App.jsx`,
`frontend/src/lib/api.js`, or `frontend/src/components/ui.jsx`, stop — you are
about to create a merge conflict with the eleven other agents working right
now, and that is the specific failure this contract exists to prevent.

The host discovers your files automatically. There is no registration step.

## Shared files — do not touch

| File | Why it is shared |
|------|------------------|
| `backend/dsr/api.py` | Discovers and mounts every feature router |
| `backend/dsr/deps.py` | The dependency seam every feature imports from |
| `frontend/src/App.jsx` | Builds its nav from the discovered feature list |
| `frontend/src/lib/api.js` | Exposes `apiRequest` for all features |
| `frontend/src/components/ui.jsx` | UI primitives; use `Icon path=` for new glyphs |
| `backend/dsr/db/audited.py` | The audit wrapper; the product guarantee lives here |
| `backend/dsr/store.py` | Generic schema-flexible storage |

Needing something that these files do not provide is a design signal, not a
licence to edit them. Put it in your own module and raise it in your PR
description.

## Backend feature

Create `backend/dsr/features/<ticket>_<slug>.py`. The host imports every
non-underscore module in that package at startup and mounts its `router`.

```python
"""WF-014: expire or cap access to a room."""

from typing import Any

from fastapi import APIRouter

from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-014-access-windows",
    "ticket": "WF-014",
    "name": "Expire or cap access to a room",
    "description": "Bound a room's access by date and by view count.",
}

router = APIRouter(prefix="/api/wf-014", tags=["WF-014"])


@router.get("/summary")
def summary(store: RecordStore = StoreDep) -> dict[str, Any]:
    return {"rooms": len(store.list("room"))}
```

Rules:

- **Own your prefix.** `prefix` must be `/api/<your-ticket-slug>` and must be
  unique. The host refuses to mount a colliding prefix and reports it as a
  failed feature rather than letting you shadow someone else.
- **Import dependencies from `dsr.deps`, never from `dsr.api`.** Importing the
  app from a feature reintroduces the coupling this removes. There is a test
  that enforces this.
- **All reads and writes go through `StoreDep` / `RecordStore`.** Never open
  the SQLite file yourself. The audit row is written in the same transaction as
  the change, and that is the guarantee the product is built on.
- **Payloads stay schema-flexible.** Store arbitrary JSON in `data` via
  `store.create(collection, {...})`. Do not add a migration or a typed column
  for your team's field.

## Frontend feature

Create `frontend/src/features/<id>/index.jsx`. Vite expands the glob in
`src/lib/features.js` at build time, so App.jsx never learns your name.

```jsx
import { apiRequest } from '@/lib/api'
import { Card, Spinner, useAsync } from '@/components/ui'

function AccessWindowsPage() {
  const { data, loading, error, refetch } = useAsync(() => apiRequest('/wf-014/summary'), [])
  if (loading) return <Spinner label="Loading access windows" />
  if (error) return <ErrorNote error={error} onRetry={refetch} />
  return <Card>{data.rooms} rooms</Card>
}

export default {
  id: 'wf-014-access-windows',
  label: 'Access windows',
  icon: 'audit',
  order: 140,
  Component: AccessWindowsPage,
}
```

Rules:

- **Import with `@/`, not relative paths.** `@` is aliased to `src`, so your
  import works no matter how deep your folder is.
- **Default export is the descriptor**, exactly `{ id, label, icon, Component }`
  plus optional `order` and `iconPath`. A named-export component is not
  discovered.
- **`id` must be unique** and match your backend feature id. Duplicates are
  reported in the UI, not silently dropped.
- **Call your own API through `apiRequest`.** Do not add a method to `api`.
- **Need an icon that isn't in the set?** Pass `path`:
  `<Icon path="M12 2 2 7l10 5 10-5-10-5z" />`. Do not add to `PATHS`.
- Meet the design floor in `design-system/digital-sales-room/MASTER.md`: 44px
  touch targets, visible focus, text label beside every icon, no emoji as
  icons, `prefers-reduced-motion` respected.

## Tests

Add `backend/tests/test_<ticket>.py`. Tests use a temporary database — point
`DSR_DB_PATH` at one, as `test_features.py` does. A feature without tests is
not finished.

Run:

```sh
cd backend && ../.venv/Scripts/python -m pytest
```

## When your feature is broken

A feature that raises on import is recorded in the registry's `failed` list,
reported at `/api/features` and rendered in the UI, and skipped. The rest of the
product keeps working. That is deliberate: with a hundred independently-authored
modules, one bad file must not be able to take the app offline. It is not an
excuse to ship a broken feature — the failure is visible by design.

## Checklist

- [ ] `backend/dsr/features/<ticket>_<slug>.py` with `FEATURE` and a unique `router.prefix`
- [ ] `frontend/src/features/<id>/index.jsx` with a default-export descriptor
- [ ] `backend/tests/test_<ticket>.py` passing against a temp database
- [ ] `git diff` touches **no** shared file listed above
- [ ] Jev gate returned `pass` before merge
