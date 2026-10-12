# Worker Onboarding Inventory

Runbook: `Agent oriented setup.txt` (multi-worker hub-and-spoke, Orca ADE)
Hub: `pc-max` / `DK-DELL` / Tailscale 100.71.15.24
Agent: Space Bunny, working through `Agent oriented setup.txt` phases A-G.

## Hub (verified 2026-10-12)

```
Tailscale name:   pc-max
Windows hostname: DK-DELL
Windows account:  DK-DELL\dilip
Tailscale IP:     100.71.15.24
Orca version:     1.4.224
Orca runtime ID:  26e6acb0-451f-4781-9005-265133f28995
Orca state:       ready / reachable / connected
SSH client:       OpenSSH_for_Windows_9.5p2, LibreSSL 3.8.2
Hub public key:   ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILqPzhoHFrdJnn8AYY/MEHbewaZVO+39nqMV4YjJkWwq orca-hub
                  (local only - never copy the private key to a worker)
```

## Existing spoke (baseline, re-verified as working)

```
Tailscale name:   pc-high
Windows hostname: DILIP-PC
Tailscale IP:     100.68.167.99
Environment name: pc-high
Environment ID:   40f3576e-ee17-4afe-9049-16741d0f9829
Runtime ID:       2269885e-67b6-4141-9f65-a8312a5c3cf7
Endpoint:         ws://100.68.167.99:6768
Repo ID:          3d7942a2-b055-452b-bf2b-078f452345ee
Repo path:        C:/Users/Dilip/Documents/Repo/client-theater
Git remote:       github.com/n-dilipkumar/client-theater
TCP 22:           reachable
Status:           BASELINE OK (repo list --environment pc-high returns the repo)
```

Purpose of this run: use the proven pc-high spoke as the recipe to onboard a NEW spoke.

## Target spoke (in progress)

```
Tailscale name:   pc-mini  (full: pc-mini.tail4997e0.ts.net)
Tailscale IP:     100.78.82.20   (matches hub's view; pong via IPv6, 52ms)
Windows hostname: DILIP-MINI-DELL
Windows account:  DILIP-MINI-DELL\Dilip
shell elevated:   True
ssh_port:         22
orca_version:     1.4.224          (same as hub)
orca_path:        C:\Users\Dilip\AppData\Local\Programs\orca\resources\bin\orca.exe
orca_environments_on_worker: []     (expected - worker is the target, hub holds the env)
opencode_version: v2.0.25          (worker 1 has 2.0.26 - minor, acceptable)
opencode_path:    C:\Users\Dilip\AppData\Roaming\npm\opencode.ps1   (a .ps1 shim)
repo_path:        C:\Users\Dilip\Documents\git repos\client-theater
repo_HEAD:        3c1585edee8039d1f4b9aed1505a41ddf3ece612
repo_branch:      main
repo_upstream:    main...origin/main (no ahead/behind divergence reported)
repo_dirty:       YES - untracked "Agent oriented setup.txt" only. MUST PRESERVE.
git_remote:       origin https://github.com/n-dilipkumar/client-theater.git
free_disk_C:      38.32 GB
net_profile:      Tailscale=Private (good), Wi-Fi=Public
orca_environment_name: pc-mini
orca_environment_id:   92d89f5e-a84a-4024-8c23-887293e7306b
paired_device_id:      de856624-e31e-4244-8474-17ae493dc5b6
endpoint:              ws://100.78.82.20:6768
remote_runtime_id:     66d293eb-b146-407e-ada2-ea114a109e82
remote_repo_id:        e6bfdab6-cc8e-49dd-8e7a-1ce6c4153315
remote_repo_path:      C:/Users/Dilip/Documents/git repos/client-theater
remote_repo_remote:    github.com/n-dilipkumar/client-theater
dedicated_worktree_root: C:\Users\Dilip\orca\workspaces\client-theater
validation_status:     IN PROGRESS
```

### Stage 4 result - isolation

```
Worktree path : C:\Users\Dilip\orca\workspaces\client-theater\pcm-mini-w1
Worktree id   : e6bfdab6-cc8e-49dd-8e7a-1ce6c4153315::C:/Users/Dilip/orca/workspaces/client-theater/pcm-mini-w1
Instance id   : a4b9e0dc-0807-432d-aac8-316c0686cbca
Branch        : pcm-mini-w1
Base ref      : refs/remotes/origin/main
Base commit   : 3c1585edee8039d1f4b9aed1505a41ddf3ece612
Initial status: clean (## pcm-mini-w1, no modifications)
setup         : skip (no dependency install performed)
parent        : none (--no-parent)
creator       : paired-device de856624-e31e-4244-8474-17ae493dc5b6
warnings      : []
```

Confirmed by running git ON pc-mini over SSH, not merely trusting the create
response:

```
ORIGINAL REPO   HEAD 3c1585ed  branch main
                status ## main...origin/main
                      ?? "Agent oriented setup.txt"      <- preserved, untracked

WORKTREE pcm-mini-w1  HEAD 3c1585ed  branch pcm-mini-w1
                status ## pcm-mini-w1                    <- clean

git worktree list:
  C:/Users/Dilip/Documents/git repos/client-theater         3c1585e [main]
  C:/Users/Dilip/orca/workspaces/client-theater/pcm-mini-w1 3c1585e [pcm-mini-w1]
```

Two worktrees now exist in that repo, so the original checkout is being used
as the main worktree and the agent workspace is genuinely separate. The
worktree branch diverges from `main` immediately, which is what prevents two
concurrent agents from colliding.

### Stage 3 result - pairing notes worth keeping

The pairing code was generated through the ADE UI rather than the scripted
path: `Settings -> Remote Orca Servers -> Share this host -> Another Device ->
Tailscale -> 100.78.82.20 -> Generate Access link`. That is the reliable
route and avoids depending on log scraping.

Two deviations from the scripted STAGE3, both deliberate and both benign:

1. `port 6768 ALREADY LISTENING - a server is already running`. The guard
   did its job: it refused to start a second server and left the existing
   one alone. No duplicate Orca runtime was spawned.
2. The listener was bound to `127.0.0.1:6768` only, and no
   `orca-serve.log` existed, so the script's regex had nothing to match.
   Pairing via the UI produced the code regardless, because the code carries
   the advertised endpoint explicitly rather than being scraped from a log.

**CLI pitfall (hub-side, will recur on any future spoke):** a 326-character
base64 payload passed as `--pairing-code` through this agent shell's argument
handling is silently truncated or mangled, and Orca answers with a generic
`invalid_argument / Invalid pairing code. Expected an orca://pair?... URL or
bare pairing payload.` The same code decodes to perfectly valid JSON
(`endpoint`, `deviceToken`, `publicKeyB64`, `pairedDeviceId`, `scope:
runtime`), which proves the code was never wrong. Passing the identical string
via .NET `ProcessStartInfo.Arguments` (no shell interpolation) succeeded
immediately. For any long or URL-bearing Orca argument, use `ProcessStartInfo`
rather than a direct shell invocation.

### Stage 1 findings that change the plan

1. **OpenSSH Server capability is already `Installed`.** Runbook Error A
   (capability `Staged`) and Error B (servicing hang) do NOT apply. No DISM,
   no `Add-WindowsCapability`. The service simply exists but was `Stopped`
   with `StartType=Manual`. Stage 2 only sets Automatic + starts it.
2. **Firewall rule `OpenSSH-Server-In-TCP` already exists**, Enabled=True,
   Action=Allow, Profile=Any. Stage 2 must NOT create a duplicate rule.
3. **No `authorized_keys` exists** in either location, so nothing can be
   overwritten or duplicated. Stage 2 creates the correct one.
4. **Account-class detection is required.** `DILIP-MINI-DELL\Dilip` is
   elevated, which does not by itself prove membership in the built-in
   Administrators group (S-1-5-32-544). Windows OpenSSH silently ignores
   `%USERPROFILE%\.ssh\authorized_keys` for Administrators members and reads
   `C:\ProgramData\ssh\administrators_authorized_keys` instead. Stage 2 tests
   group membership and picks exactly one location - the runbook's "do not
   write to both" rule is respected by detecting rather than guessing.
5. **Same base commit as the pc-high worktrees**
   (`3c1585edee8039d1f4b9aed1505a41ddf3ece612`), so this spoke is a genuine
   peer, not a fork.
6. **Tailscale adapter is `Private`** network category, so the Tailscale
   interface is not blocked by a Public-profile firewall default. Port 6768
   for `orca serve` will need its own inbound rule.

### Target spoke progress log

| Phase | Step | Result |
|-------|------|--------|
| A | Identify worker | PASS - DILIP-MINI-DELL / Dilip / 100.78.82.20 |
| A | Orca / OpenCode versions | PASS - orca 1.4.224, opencode v2.0.25 |
| A | OpenSSH server presence | PASS - capability Installed (no install needed) |
| A | Repo path / git state | PASS - captured; dirty = untracked setup txt |
| B | Tailscale connectivity | PASS - pong via IPv6 |
| B | OpenSSH Server install | N/A - already installed |
| B | sshd service running/auto | PASS - Running / Auto |
| B | Firewall + listener | PASS - rule reused, listening :::22 and 0.0.0.0:22 |
| B | Hub public key authorized | PASS - 1 key in administrators_authorized_keys |
| B | Passwordless SSH (BatchMode) | PASS - BatchMode hostname + whoami, no prompt |
| C | Orca environment (pairing code) | PASS - pc-mini / 92d89f5e-a84a-4024-8c23-887293e7306b |
| C | Remote repo discovery | PASS - repo e6bfdab6 at C:/Users/Dilip/Documents/git repos/client-theater |
| D | Isolated worktree creation | PASS - pcm-mini-w1 @ 3c1585ed, clean |
| E | Task create / dispatch / report | PASS - task_fe8a06fcde33 completed, outcome succeeded |
| F | Concurrency validation | PASS - 44.3s measured simultaneous-active window across 2 physical spokes |
| G | Final classification | READY |

### Stage 5 - dispatch failure and recovery (runbook Error D / new pitfall)

First dispatch attempt FAILED:

```
state:     failed
stage:     remote_attach
lastError: Agent launcher opencode is disabled or unavailable.
effects:   []
residualResources: []
```

`effects: []` and `residualResources: []` proved no worktree, terminal, or
partial dispatch was created, which is what made a retry safe rather than a
guess.

**Root cause found in Orca's own settings on pc-mini**
(`%APPDATA%\orca\profiles\local-default\orca-data.json`):

```
"defaultTuiAgent"   : "opencode2"
"disabledTuiAgents" : "opencode"
```

The TUI agent id `opencode` is explicitly disabled on this machine and
`opencode2` is the enabled alias. Both resolve to opencode v2.0.25
(`opencode.cmd` and `opencode2.cmd` in `AppData\Roaming\npm`), so the CLI is
present and working - Orca's registry is what rejected the id. This is a
deliberate local preference, NOT a fault, so `disabledTuiAgents` was left
untouched and the enabled id was used instead.

The failed task went terminal (`status: failed`), so per Error D a NEW task
was created rather than force-starting the old one.

### Stage 5 result - successful dispatch

```
Run ID        : run_c70128c39194
Task ID       : task_fe8a06fcde33   (supersedes failed task_79cc7e81d8e7)
Dispatch ID   : ctx_300436444d8c
Terminal      : term_3f0a18cc-7d9c-4897-9927-6ace9ff26fa5
Agent         : opencode2 (requested == effective)
Environment   : pc-mini / 92d89f5e-a84a-4024-8c23-887293e7306b
Worktree      : reused (pcm-mini-w1)
setup         : not_applicable (no dependency install)
dispatch state: ready / input_accepted
durable status: completed
outcome       : succeeded
completed_at  : 2026-10-12T03:58:02.868Z
filesModified : []
```

The worker's durable report (not the screen) states: host `DILIP-MINI-DELL`,
cwd `C:\Users\Dilip\orca\workspaces\client-theater\pcm-mini-w1`, branch
`pcm-mini-w1`, HEAD `3c1585edee8039d1f4b9aed1505a41ddf3ece612`, porcelain
empty. Backend entry `backend/dsr/api.py`; frontend entries
`frontend/index.html` -> `frontend/src/main.jsx` -> `frontend/src/App.jsx`.

It also volunteered two real operational caveats worth recording:
1. Branch `pcm-mini-w1` has **no upstream configured**.
2. There is **no `.venv`** in the checkout, though `AGENTS.md` assumes
   `./.venv/Scripts/python`. Any follow-up task that runs pytest or npm must
   resolve that environment first. (Per the runbook, dependency installation
   was deliberately out of scope for onboarding.)

### Post-dispatch verification run ON pc-mini over SSH

```
ORIGINAL REPO  HEAD 3c1585ed  branch main
               status ## main...origin/main
                     ?? "Agent oriented setup.txt"      <- preserved, unchanged

AGENT WORKTREE HEAD 3c1585ed  branch pcm-mini-w1
               status ## pcm-mini-w1
               status --porcelain=v1 -> <empty>        <- no files changed

worktree list  (still exactly 2)
  C:/Users/Dilip/Documents/git repos/client-theater         3c1585e [main]
  C:/Users/Dilip/orca/workspaces/client-theater/pcm-mini-w1 3c1585e [pcm-mini-w1]
```

The agent's own claim `filesModified: []` is corroborated by git on the
worker itself rather than taken on trust.

### worker-read truncation (runbook Error F, confirmed)

`worker-read` returned `sourceExact: false`, `contentComplete: false`,
`clipping: [terminal_buffer, terminal_fallback]`,
`fallbackReason: provider_unsupported`. The screen tail was partial and
misleading enough that the durable `task-list` result had to be polled for
completion instead. Terminal status stayed `running` well after the worker
reported success - not a failure signal.

---

## Final report (runbook section 11 format)

```
Worker onboarding report - pc-mini (spoke #2)

Identity
  Tailscale name:   pc-mini  (pc-mini.tail4997e0.ts.net)
  Tailscale IP:     100.78.82.20
  Windows hostname: DILIP-MINI-DELL
  Windows account:  DILIP-MINI-DELL\Dilip

Software
  OpenSSH Server state: Installed (pre-existing; no install performed)
  sshd service state:   Running / Automatic
  SSH listener state:   :::22 and 0.0.0.0:22
  Orca version:         1.4.224
  OpenCode version:     v2.0.25 (as agent id "opencode2"; id "opencode" is
                        disabled in this machine's Orca settings)

Connectivity
  Tailscale reachability: PASS (pong via IPv6, 52ms)
  Passwordless SSH:        PASS (BatchMode=yes, no prompt)
  Remote command execution: PASS (git worktree list run over SSH)

Orca
  Environment name:  pc-mini
  Environment ID:    92d89f5e-a84a-4024-8c23-887293e7306b
  Runtime ID:        66d293eb-b146-407e-ada2-ea114a109e82
  Endpoint:          ws://100.78.82.20:6768
  Remote repository discovery: PASS (repo e6bfdab6-cc8e-49dd-8e7a-1ce6c4153315)

Isolation
  Worktree path:    C:\Users\Dilip\orca\workspaces\client-theater\pcm-mini-w1
  Branch:           pcm-mini-w1 (no upstream configured)
  Base commit:      3c1585edee8039d1f4b9aed1505a41ddf3ece612
  Initial Git status: clean
  Original checkout preserved: YES (main @ 3c1585ed, untracked
                     "Agent oriented setup.txt" still present)

Execution
  Task ID:            task_fe8a06fcde33 (supersedes failed task_79cc7e81d8e7)
  Dispatch ID:        ctx_300436444d8c
  Terminal handle:    term_3f0a18cc-7d9c-4897-9927-6ace9ff26fa5
  Agent:              opencode2
  Worker result:      succeeded
  Durable task status: completed (2026-10-12T03:58:02.868Z)
  Report retrieval:    OK from durable task result; worker-read was truncated

Concurrency evidence
  Second worker/task:  pc-high already carries two proven parallel worktrees
                       (parallel-worker-a / parallel-worker-b)
  Overlapping active states observed: NOT re-observed in this run. This
                       onboarding validated dispatch/report on a NEW spoke;
                       a two-spoke overlap test is still unrun.
  Completion verified: YES (git porcelain empty on the worker)

Safety
  Files modified:              none (filesModified: [] and porcelain empty)
  Dependencies installed:      none (--setup skip; setup not_applicable)
  Existing uncommitted changes preserved: YES (untracked setup txt intact)
  Residual resources:          [] (clean on both failed and successful dispatch)
  Outstanding errors:          none blocking

Final classification
  READY
  All Phase A-E gates pass for pc-mini: Tailscale, passwordless SSH, saved
  Orca environment, remote repo discovery, isolated clean worktree, bounded
  read-only task dispatched, durable completion, and no repository mutation.
  Phase F cross-spoke concurrency overlap remains the one open item.
```

## Reusable lessons for spoke #3

1. **Orca environment registration is pairing-code based in 1.4.224**, not
   endpoint entry. Get the code from the ADE UI:
   Settings -> Remote Orca Servers -> Share this host -> Another Device ->
   Tailscale -> <worker-ip> -> Generate Access link.
2. **Pass long Orca arguments via .NET `ProcessStartInfo`, not the shell.**
   A 326-char base64 pairing code was silently mangled by the agent shell and
   rejected with a misleading "Invalid pairing code" message.
3. **Detect Administrators-group membership before choosing the
   `authorized_keys` file.** Do not write both. Use SIDs, not group names,
   for ACLs so they survive a localized Windows install.
4. **Check `settings.disabledTuiAgents` and `defaultTuiAgent` on the worker
   before dispatching with `--agent opencode`.** The id may be disabled there
   even when the CLI is installed and healthy. Use the enabled id instead of
   changing the user's preference.
5. **Verify claims with git over SSH, not from the agent's own report.**
6. Treat `effects: []` + `residualResources: []` on a failed dispatch as proof
   it is safe to retry; treat `ready`/`input_accepted` as acceptance only,
   never as completion.
7. `worker-read` truncates (`sourceExact: false`). Poll `task-list` for the
   durable result instead.

---

# Model availability and live round-trip verification

## Model catalogue present on BOTH spokes

`opencode models` is not implemented in opencode v2.0.25 (it returns nothing
and a non-zero exit), so the catalogue was read from each spoke's own
`~/.local/share/opencode/opencode.db` instead of assumed.

```
pc-mini  (DILIP-MINI-DELL)          pc-high  (DILIP-PC)
  opencode/space-bunny-free          opencode/space-bunny-free
    variant: max                        variant: max
  opencode/step-5-preview-free       opencode/step-5-preview-free
    variant: high                       variant: high
```

Both spokes expose the identical pair, and both ids match the model this hub
session itself runs on (`opencode/space-bunny-free#max`, per `AGENTS.md`).

## Live round trip: pc-mini / space-bunny-free - CONFIRMED

Prompt `hi`, run on pc-mini via SSH:

```
exit code : 0
stdout    : Hi! What can I help you with today?
stderr    : build ┬╖ space-bunny-free        <- model banner confirms which model served it
elapsed   : ~3 min on first invocation
```

The ~3 minute first-call duration is model download/warm-up, not a hang. Two
earlier attempts were abandoned by the *caller's* timeout while the child was
still working correctly - worth recording because "slow" and "broken" look
identical from the outside. Probes now write results to a log file and run
detached, so a long model call is never mistaken for a failure.

## Live round trips - ALL FOUR CONFIRMED

Executed as Orca-dispatched agents on each spoke (not ad-hoc SSH), so the
model call and the report both travel the real orchestration path.

| Spoke | Model | Prompt | Exit | Reply (verbatim) | Verdict |
|---|---|---|---|---|---|
| pc-mini | `opencode/space-bunny-free` | `hi` | 0 | `I'm here. What would you like to work on?` | **PASS** |
| pc-mini | `opencode/step-5-preview-free` | `hello` | 0 | `Hello! I'm ready to help you with work in this repository (client-theater/mdl-mini).` + bullets | **PASS** |
| pc-high | `opencode/space-bunny-free` | `hi` | 0 | long analysis (see below) | **PASS** |
| pc-high | `opencode/step-5-preview-free` | `hello` | 0 | `Hey! What can I help you with today?` | **PASS** |

Tasks: `task_31484b5ffa82` (pc-mini, completed 04:37:08.858Z),
`task_181c431eee63` (pc-high, completed 04:37:35.307Z). Both
`outcome: succeeded`, worktree porcelain empty on both spokes afterwards.

**The models are demonstrably real, not echoing stubs.** pc-mini's
`step-5-preview-free` reply named the actual worktree
`client-theater/mdl-mini` and summarised this repo's own AGENTS.md contract
(feature plugins, the Jev validator, the test command) - it had to read the
repo to answer. pc-high's `space-bunny-free` independently investigated and
reported that `TASK.md` at repo root is a stale already-merged WF-106 brief
(`15eedb6` is an ancestor of HEAD) and that the venv is missing. Neither is
possible without a working model.

### Model behaviour difference worth knowing

Both ids resolve to opencode v2.0.25, but they behave very differently on the
same one-word prompt:

- `space-bunny-free` **acts as a coding agent**: it starts working the repo
  unprompted. On pc-high, `hi` turned into a full audit. Its first token was
  `I'll take a look at the workspace to get oriented before we start.`
- `step-5-preview-free` **answers conversationally**: `hi`/`hello` return a
  short greeting.

So "model works" and "prompt returns a greeting" are different claims. A
greeting test on `space-bunny-free` is not evidence of a broken model - it is
that model doing its job. This also means an unattended `hi` to
`space-bunny-free` can burn tokens wandering the repo; prompt it explicitly.

### Timing (varies by cache state, not a reliability signal)

```
first-ever call on a spoke   ~3 min   (model download/warm-up)
pc-mini, warm cache           ~8-9 s
pc-high, colder cache        ~41-77 s
```

### Two harness-caused, non-blocking anomalies (recorded, not hidden)

1. pc-mini saw two `Tool execution interrupted: shell` server restarts abort
   in-flight calls. It re-ran each model once for a clean exit code. This is
   the harness reaping processes, the same behaviour that makes
   `BSK_AUTO_START=0` mandatory on this host - not a model fault.
2. pc-high's `space-bunny-free` run logged one recoverable error:
   `This non-interactive run cannot ask the user questions, so the question
   was cancelled.` Exit code was still 0 and output non-empty.

Neither affects the PASS verdicts; both are disclosed because a clean-looking
pass that hides an error is worse than an ugly one that explains itself.

### Worktree safety during model runs

`pc-high`'s task reported `filesModified` with one entry, which needed
checking rather than trusting:

```
C:\Users\Dilip\AppData\Local\Temp\opencode\model-roundtrip-task_181c431eee63.md
```

Verified on pc-high: the file exists, is **outside** the worktree, and
`mdl-high` porcelain is **0 entries** at HEAD `3c1585ed`. A run that reports
`filesModified` is not automatically a violation - it is a violation only if
the path is inside the worktree.

## Incidental findings from the model runs (pre-existing, untouched)

pc-high's `space-bunny-free` surfaced three things about the repo that this
onboarding did not cause and did not change:

1. **`TASK.md` is committed to `main` and is a stale, already-merged brief.**
   It was added by merge commit `15eedb6` (PR #250, WF-106), names a deleted
   worktree `q-ticket-31`, and would cause a duplicate ticket to be
   dispatched. This is the same `TASK.md` present in the hub checkout.
2. **The venv is missing on both spokes.** Neither `.venv/Scripts/python` nor
   `dsrvenv` exists on pc-high; `pytest`/`ruff`/`uvicorn` are not importable.
   The AGENTS.md-mandated `cd backend && ../.venv/Scripts/python -m pytest`
   is therefore **unrunnable on the workers** as they stand. This was already
   flagged during pc-mini onboarding and is now confirmed on pc-high too.
   Dependency installation stayed out of scope by the runbook, so this is
   unresolved, not fixed.
3. **Possible repo hygiene issues, text-matched and unconfirmed** by that
   agent: 13 files containing non-cp1252 glyphs (`->`, checkmarks) that
   `TASK.md` warns can break the Windows seeder, and 9 route prefixes declared
   more than once. Both were explicitly flagged as unconfirmed text matches.

---

# Blockers fixed, parity achieved

## What was broken and what changed

| Blocker | Before | After |
|---|---|---|
| no venv on pc-mini | `../.venv/Scripts/python -m pytest` unrunnable | canonical venv, suite green |
| no worktree venv **anywhere** | every agent worktree was test-incapable | junction links in **382 ms** |
| `httpx` missing on spokes | `starlette.testclient` raised at import | `httpx 0.28.1`, TestClient imports |
| no frontend deps on pc-mini | `npm test` impossible | `npm ci`, 132 MB canonical |
| Jev unreachable from spokes | no bridge, no key | hub bridge over Tailscale |
| 3/7 global skills on spokes | `typesafe-ai`, `computer-use`, `herdr`, `ste100` missing | 7/7 on both |
| no autonomy contract | spokes had no stated limits | `SPOKE-MANUAL.md` on both |

### Shared environment + junction: the core change

Per-worktree installs were minutes each and would have scaled with
parallelism, which is exactly the overhead to avoid. Instead each spoke owns
one canonical environment and every worktree gets a junction:

```
C:\Users\Dilip\orca-envs\venv           (one per spoke)
C:\Users\Dilip\orca-envs\node_modules   (one per spoke)

powershell -File C:\Users\Dilip\orca-envs\bin\link-worktree-env.ps1 -Worktree <path>
```

Measured:

```
link two directories        382 ms  (pc-mini)
                            1165 ms (pc-high)
venv creation (once)        14 s / 31 s
backend install (once)      58 s / 330 s
npm ci + canonical copy     44 s / 99 s
FULL backend suite          133 tests, 458 s, GREEN on pc-mini
```

Dependency install used the existing `backend\pyproject.toml` editable
install - there is no `requirements.txt` in this repo, and the hub venv
carries `dsr==0.1.0`. Original checkouts were **not** modified; where
node_modules already existed on pc-high it was **copied** to canonical, not
moved.

### Jev bridge: one key, one process, two binds

The bridge bound to `127.0.0.1:8777`, so no spoke could reach it. Changed
`server.mjs` to honour `JEV_BRIDGE_HOST` (defaulting to loopback, so hub-local
opencode/Hermes/Pi are unaffected).

A first attempt tried to call `server.listen()` twice for a dual bind. That
**does not work** - an `http.Server` listens once - and it silently broke
loopback-only access, breaking hub-local tools until it was caught. Corrected
to run **two processes**:

```
127.0.0.1:8777      pid 47896   -> hub-local tools, unchanged behaviour
100.71.15.24:8777   pid 15368   -> spokes
```

Verified from **both** spokes: `GET /v1/models -> jev-latest, jev-1.13.0`.
No firewall change was needed - Tailscale's interface rules already permit
it. The API key exists only on pc-max.

### Jev autonomy verified live from both spokes

A real architecture question, decided on each spoke with no human:

```
pc-mini  verdict=uncertain  selected=two_routes
         reason: confidence 0.40 < 0.75; options too close to decide
pc-high  verdict=uncertain  selected=two_routes
         reason: confidence 0.56 < 0.75; options too close to decide
```

Both spokes independently reached Jev through the hub bridge and returned
calibrated answers. Both landed on `uncertain`, which under the contract
means **stop and escalate**, not proceed. Two different confidence values for
the same question confirm these are live judgments, not a canned response.

### Skills parity

Skills exist in two places. The in-repo 46 (`.agents/skills` and
`.claude/skills`) travel with every worktree automatically. The 7 global
ones are machine-local and did **not** travel:

```
before  pc-mini: orca-cli, orchestration, find-skills            (3/7)
after   both   : computer-use, find-skills, herdr, orca-cli,
                 orchestration, ste100, typesafe-ai              (7/7)
```

Synced as a zip, installed with `robocopy`, existing copies never
overwritten.

### Autonomy contract

`SPOKE-MANUAL.md` is copied to `C:\Users\Dilip\orca-envs\SPOKE-MANUAL.md` on
both spokes and states, in the agent's own terms: what it decides alone, what
escalates, the escalation format (one bounded message, batched questions),
the forbidden git operations, the shared files it must not touch, and the
rule that the TypeSafe key never leaves the hub.

---

# Phase F - cross-spoke concurrency overlap (PROVEN)

The open item from the pc-mini onboarding has now been closed with observed
evidence rather than inference.

## Method

Two rounds were run. Round 1 proved the pair co-existed but was measured with
a wrong JSON shape; round 2 is the instrumented measurement that counts.

Round 2 setup:

- One **fresh** worktree per physical spoke, both from `origin/main`:
  - pc-mini: `ovl2-mini`  (runtime 92d89f5e / 66d293eb)
  - pc-high: `ovl2-high`  (runtime 40f3576e / 2269885e)
- Identical task spec on both, deliberately substantive so the agents are
  genuinely working at the same time rather than finishing instantly.
- Same agent id `opencode2` on both, so the two runs are directly comparable.
- Both dispatched **back-to-back** with no wait between them:
  ```
  04:07:39.927Z  dispatch A issued (pc-mini)  -> returned 04:07:48.659Z
  04:07:48.664Z  dispatch B issued (pc-high)  -> returned 04:07:56.167Z
  ```
  5ms apart at the instant the first returned - no serialisation.

## Observed simultaneous-active window (lockstep probes)

Each probe iteration queried pc-mini and then pc-high back-to-back, so both
states were observed within a bounded gap. `live` is
`projection.liveness.verdict`.

```
[04:08:06.656 -> 04:08:07.963] MINI dispatched live || HIGH dispatched live   BOTH ACTIVE
[04:08:15.054 -> 04:08:17.406] MINI dispatched live || HIGH dispatched live   BOTH ACTIVE
[04:08:24.576 -> 04:08:25.857] MINI dispatched live || HIGH dispatched live   BOTH ACTIVE
[04:08:33.104 -> 04:08:34.496] MINI dispatched live || HIGH dispatched live   BOTH ACTIVE
[04:08:41.692 -> 04:08:42.877] MINI dispatched live || HIGH dispatched live   BOTH ACTIVE
[04:08:50.048 -> 04:08:51.005] MINI dispatched live || HIGH dispatched live   BOTH ACTIVE
[04:08:57.734 -> 04:08:59.260] MINI completed/succeeded || HIGH dispatched    diverged
[04:09:06.084 -> 04:09:07.064] MINI completed/succeeded || HIGH dispatched
[04:09:18.170 -> 04:09:19.592] MINI completed/succeeded || HIGH dispatched
[04:09:26.635 -> 04:09:27.626] MINI completed/succeeded || HIGH completed     both settled
```

```
samples with BOTH tasks simultaneously 'dispatched' : 6
first simultaneous observation : 04:08:06.656Z .. 04:08:07.963Z
last  simultaneous observation : 04:08:50.048Z .. 04:08:51.005Z
conservative overlap span      : 00:00:44.349
max intra-iteration probe gap  : 5102 ms
```

The overlap span is stated **conservatively**: it runs from the first probe of
pc-mini in the first simultaneous iteration to the last probe of pc-high in
the last simultaneous iteration, so it can only understate the true interval.
Both tasks were non-terminal and both workers reported `liveness: live`
throughout.

## Completion on two different physical machines

```
task_af0efd3ffa35  pc-mini  completed  2026-10-12T04:08:56.065Z  succeeded
task_4f3d79855b08  pc-high  completed  2026-10-12T04:09:25.976Z  succeeded
filesModified      : []  and  []
```

The two machines independently converged on identical facts - same HEAD
`3c1585ed`, same 10 top-level directories, same **886** files under
`backend/` and **488** under `frontend/src/`, same FastAPI entry point
`backend/dsr/api.py` with `app = FastAPI(...)` at line 50 - while reporting
their own distinct hostnames (`Dilip-Mini-Dell` vs `Dilip-PC`) and distinct
branches (`ovl2-mini` vs `ovl2-high`). Matching numbers from two independent
hosts is corroboration that neither was reading the other's machine.

## Safety verification, run on each worker over SSH

```
pc-mini  ovl-mini-1  3c1585ed [ovl-mini-1]  porcelain entries: 0
pc-mini  ovl2-mini   3c1585ed [ovl2-mini]   porcelain entries: 0
pc-high  ovl-high-1  3c1585ed [ovl-high-1]  porcelain entries: 0
pc-high  ovl2-high   3c1585ed [ovl2-high]   porcelain entries: 0

pc-mini  ORIGINAL  main @ 3c1585ed  ## main...origin/main
                              ?? "Agent oriented setup.txt"
pc-high  ORIGINAL  main @ 3c1585ed  ## main...origin/main
                              M .opencode/agent/ui-verifier.md
                              M AGENTS.md
```

Every agent worktree is byte-identical to creation. Both original checkouts
are untouched, including pc-high's pre-existing uncommitted edits in
`.opencode/agent/ui-verifier.md` and `AGENTS.md`, which predate this work and
were never at risk because agents never ran in the original checkout.

## Classification

**Phase F PASS. Cross-spoke concurrency is PROVEN.** Two agents ran
simultaneously on two separate physical Windows machines, in two separate
Orca environments, in two separate isolated worktrees, driven by one hub -
with a measured 44.3s simultaneous-active interval and no mutation to either
repository.

The full three-layer model from the runbook's opening now holds for both
spokes:

| Layer | pc-mini | pc-high |
|---|---|---|
| Network + SSH | PASS | PASS |
| Orca environment + worktree | PASS | PASS |
| Agent dispatch + completion | PASS | PASS |
| Cross-spoke overlap | **PASS (observed)** | **PASS (observed)** |

## Two loose ends found, neither caused by this setup

1. **`task_252b77aa255e` (round 1, pc-mini) is still `dispatched`** with
   `result: null` - no completion, no failure, no error. Its dispatch
   `ctx_f33cbac00ffd` still reports `worker.state: ready` /
   `stage: input_accepted`. Round 1's task spec told the agent not to create
   files but gave it no scratch location outside the worktree; the pc-high
   twin in the same round wrote its evidence to
   `%TEMP%\opencode\readonly-inspection-ovl-high-1.md` and completed fine.
   The likely cause is that agent stalling on where to put evidence. Left
   untouched deliberately, per Error D: it is not blocking, and force-starting
   or deleting it to make the run look clean is exactly what the runbook
   forbids. Round 2 removed the ambiguity by naming a scratch directory, and
   both round-2 agents completed.
2. **A stray zero-byte file named `0` is committed at the repo root** and is
   present on every branch of both checkouts. The pc-high agent flagged it
   unprompted and correctly noted it predates this work. Present in the
   hub's own repo root listing too. Pre-existing, unrelated to onboarding,
   and not touched here.

### Stage 2 result (verified from the HUB, not just the worker)

Account class was `Member of Admin grp : True`, so the hub key went to
`C:\ProgramData\ssh\administrators_authorized_keys` (1 key, 0 pre-existing to
preserve). ACLs granted SYSTEM (F) + Administrators (F) by SID with inheritance
removed. Had the key been written to `%USERPROFILE%\.ssh\authorized_keys`
instead, Windows OpenSSH would have ignored it for this account and presented
`Permission denied (publickey)` despite the key being visibly present.

Hub-side verification, run from pc-max:

```
Test-NetConnection 100.78.82.20 -Port 22   ->  True
ssh -o BatchMode=yes -o ConnectTimeout=10 Dilip@100.78.82.20 "hostname"
    -> Dilip-Mini-Dell
ssh -o BatchMode=yes -o ConnectTimeout=10 Dilip@100.78.82.20 "whoami"
    -> dilip-mini-dell\dilip
```

`BatchMode=yes` proves authentication completed without any interactive
prompt. Host key was auto-accepted to known_hosts on first contact.

Now that SSH is up, the hub can execute bounded remote commands directly,
which is how Stage 3 will be driven rather than by manual paste.

## Commands established for THIS Orca version (1.4.224)

Environment registration uses a **pairing code**, not a manual endpoint entry:

```
ORCA environment add --name <name> --pairing-code <code> --json
ORCA environment show --environment <selector> --json
ORCA environment list --json
```

The pairing code is produced on the remote device by `orca serve`:

```
orca serve [--port <port>] [--pairing-address <host>] [--json]
```

`--pairing-address` sets the client-advertised address; for this tailnet pass the
worker's Tailscale IP so the hub can dial `ws://<tailscale-ip>:6768`.

Note: `orca environment show pc-high` (positional) is REJECTED. Use
`--environment pc-high`.

## Safety guardrails in force

- Never copy the hub private key to a worker; only the `.pub` line is authorized.
- Never use the worker's original checkout as a disposable agent worktree.
- `--setup skip` on the initial worktree create (no dependency installs).
- Never `git reset --hard`, `git clean`, or overwrite existing Orca environments.
- One worktree per concurrently executing task.
- Reuse of the pc-high worktrees/IDs is FORBIDDEN; new identifiers only.

## Stage scripts

- `STAGE1-DISCOVERY.ps1` - read-only inventory, run on the worker
- `STAGE2-SSH.ps1`        - OpenSSH Server install/service/firewall/key auth (pending)
- `STAGE3-PAIRING.ps1`    - start `orca serve` and capture the pairing code (pending)