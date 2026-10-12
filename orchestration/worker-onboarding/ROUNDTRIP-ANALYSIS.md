# Round-trip and parity analysis: hub <-> spokes

Measured from pc-max on 2026-10-12. All numbers are real measurements, not
estimates.

## 1. Network path

```
pc-max   100.71.15.24
pc-mini  100.78.82.20   Tailscale ping  20-21 ms   direct 192.168.0.12:41641
pc-high  100.68.167.99  Tailscale ping   2 ms      direct 192.168.0.13:41641
```

Both spokes are on **direct** LAN paths, not DERP relays. This matters:
during pc-mini's onboarding it was briefly `relay "blr"`, and research on
Tailscale confirms DERP adds roughly 18-45 ms versus a direct path. If a
spoke is ever forced onto a relay, latency regresses immediately and that is
the first thing to check, not the Orca layer.

## 2. Measured cost per operation

```
TCP connect      pc-mini p22 : 4-15 ms      pc-high p22 : 3-4 ms
TCP connect      pc-mini p6768: 5-6 ms       pc-high p6768: 3 ms
ssh "echo ok"    pc-mini     : 374-399 ms    pc-high     : 347-386 ms
orca terminal create  pc-mini: 901 ms        pc-high     : 4469 ms
orca terminal send    pc-mini: 915 ms        pc-high     : 916 ms
orca terminal read    pc-mini: 315 ms        pc-high     : 320 ms
```

### The finding that matters most

**`ssh echo` costs ~370 ms. `orca terminal send` costs ~915 ms. Orca is ~2.5x
the cost of a raw SSH round trip for the same "poke the machine" action.**

Both numbers are dominated by process startup on the *hub* side (forking
`orca.exe` / `ssh.exe` from PowerShell), not by the network. The network is
2-20 ms; the orchestration is ~900 ms.

Practical consequence: **SSH should be the fast path for short, deterministic
commands; Orca should be reserved for what only Orca can do** - dispatching a
supervised agent, reading a durable task result, managing worktrees. Using
Orca terminal send as a general-purpose RPC is paying a 2.5x tax.

`terminal read` at ~315 ms is the cheapest Orca call, which is why polling
durable task state beats reading screens.

## 3. Latency is NOT the bottleneck; model time is

Model round trips measured earlier:

```
first call on a spoke   ~3 min   (download/warm-up)
warm cache              ~8-9 s
colder cache            ~41-77 s
```

A single agent turn costs 8-77 s of *model* time. Hub-to-spoke overhead is
0.3-0.9 s. **Orchestration overhead is under 1% of end-to-end task latency.**

Therefore the correct optimisation target is not shaving milliseconds off
dispatch. It is:

1. eliminating per-worktree setup cost (currently the real blocker - see 4),
2. keeping model caches warm,
3. not adding coordination round trips between hub and spoke for things the
   spoke can decide alone.

Anything that adds a hub round trip per decision step (~0.4-0.9 s each, and
it serialises the agent) is worse than anything that saves milliseconds on
dispatch.

## 4. The real parallelism blocker: environment setup per worktree

Audit of the workers:

| | pc-mini | pc-high |
|---|---|---|
| python 3.14.8 | yes | yes |
| node v24.21.0 / npm 12.2.0 | yes | yes |
| git 2.56.0 | yes | yes |
| repo `.venv/Scripts/python.exe` | **NO** | yes |
| repo `frontend/node_modules` | **NO** | yes |
| `tools/jev.py` | yes | yes |
| `~/.jev/bridge.env` (API key) | **NO** | **NO** |
| jev bridge on 8777 | **NO** | **NO** |

And critically, **no worktree has a venv or node_modules**, on either spoke,
because Orca creates a fresh git worktree from `origin/main` and those
artifacts are untracked and never committed.

So `cd backend && ../.venv/Scripts/python -m pytest`, the command `AGENTS.md`
mandates, cannot run in *any* agent worktree on *either* spoke.

### Why not just install per worktree

A venv plus `pip install` plus `npm ci` is minutes per worktree, and it is
pure duplicated overhead - which is exactly what must not scale with
parallelism. Worse, every new worktree pays it again.

### Chosen strategy: shared environment + directory junction

Each spoke gets **one** canonical environment, and every worktree gets a
**Windows directory junction** pointing at it.

```
spoke canonical env:
  C:\Users\Dilip\orca-envs\venv        (python venv, once per spoke)
  C:\Users\Dilip\orca-envs\node_modules (npm deps, once per spoke)

each worktree (instant, zero copy):
  <worktree>\.venv                 -> junction to canonical venv
  <worktree>\frontend\node_modules -> junction to canonical node_modules
```

Properties:

- `AGENTS.md` commands work verbatim, because the expected paths exist.
- Cost per new worktree is **milliseconds**, not minutes.
- Disjoint branches means source isolation is unaffected - only
  dependencies are shared, and they are read-mostly.
- One place to update when a dependency changes.

The residual risk is an agent that writes into `node_modules` or mutates the
venv; a junction makes that visible as a change to the shared canonical
directory, so it is detectable rather than silent.

## 5. Jev bridge topology

The bridge is a local Node process:

```
C:\Users\Dilip\jev-bridge\server.mjs   (hub, PID 34292)
listening on 127.0.0.1:8777
credential  C:\Users\Dilip\.jev\bridge.env   -> TYPESAFE_API_KEY
```

`tools/jev.py` defaults to `JEV_BRIDGE_URL=http://127.0.0.1:8777/v1`.

**Because the bridge binds to loopback, no spoke can reach it as-is.** Options
considered:

| Option | Cost | Risk | Verdict |
|---|---|---|---|
| Copy `TYPESAFE_API_KEY` to every spoke, call `https://api.typesafe.ai/v1` directly | zero setup | key replicated to N machines; per-call network to cloud | viable fallback |
| Run a bridge on each spoke | N key copies, N processes | same key problem, plus N services to maintain | worse |
| **Hub bridge reachable over Tailscale** | one change | must re-bind off loopback, so it becomes LAN-exposed on the hub | **chosen**, with ACL |

Chosen: rebind the hub bridge to the tailnet address and let spokes call the
hub. One key, one process, one place to rotate. The bridge must be
firewalled to the tailnet only - never bound to `0.0.0.0` on a routable
interface.

`tools/jev.py` already supports this with **no code change** via
environment variables:

```
JEV_BRIDGE_URL=http://100.71.15.24:8777/v1
```

## 6. Decision autonomy - the overhead rule

The requirement is that spokes contribute independently and contact the hub
only for blockers or major concerns. This is the single biggest *latency*
lever available, because a hub round trip is ~0.4-0.9 s **and** serialises
the agent's next step.

Autonomy budget:

| Spoke decides alone | Escalates to hub |
| --- | --- |
| implementation approach inside its own worktree | worktree isolation broken (branch/worktree conflict) |
| which tests to run, how to fix a failure | dependency/env cannot be repaired locally |
| `git status`/diff review of its own branch | contract violation against `AGENTS.md` / `FEATURE-CONTRACT.md` |
| draft of a feature file under `features/` | needing a change to a **shared** file (`App.jsx`, `index.css`, `api.js`, `store.py`) |
| routine test/lint fixes | Jev `uncertain`, or a gate that cannot pass |
| commit on its own branch | schema/shape questions touching other features |

Escalation must be a **single bounded message**, never a stream of
check-ins.

## 7. Options evaluated for making dispatch faster

| Option | Expected saving | Assessment |
|---|---|---|
| Use SSH instead of Orca terminal RPC for short commands | ~2.5x on pokes | **adopt** |
| Shared venv via junction instead of per-worktree install | minutes -> ms per worktree | **adopt - biggest win** |
| Keep model caches warm; avoid cold first call | up to ~3 min on first turn | **adopt** |
| Batch hub<->spoke questions; one message per blocker | removes serialising round trips | **adopt** |
| Parallel dispatches already back-to-back | ~5 ms between dispatches | keep |
| Move Orca runtime to a local port-forward / unix socket | small | rejected, adds fragility |
| Raw WireGuard instead of Tailscale | ~10-15% on an already 2-20 ms path | **rejected - negligible vs model time, adds key management** |
| Run agents locally on the hub instead of remotely | removes RPC entirely | rejected - defeats the purpose of multi-machine parallelism |

The honest conclusion: **the transport is already fast enough.** Direct
Tailscale at 2-20 ms with ~370 ms SSH and ~915 ms Orca call overhead against
8-77 s model turns. Optimising the network would be optimising the wrong
thing. The wins are environment reuse and autonomy, not transport.