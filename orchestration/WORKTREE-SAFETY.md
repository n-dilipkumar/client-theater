STAY SAFE IN YOUR WORKTREE. Five rules. All four agents must follow these.

These are not style suggestions. One agent has already lost a day of work to rule 1.

RULE 1. NEVER RUN THESE COMMANDS IN YOUR OWN WORKTREE.

  git stash
  git reset
  git reset --hard
  git checkout --
  git checkout <branch>
  git restore
  git clean

They discard work wholesale. Your working tree is the ONLY copy of your work.
There is no second copy. Do not find out the hard way.

An agent raised this as a contradiction: Rule 1 bans `git checkout` while the
recovery section tells an agent to recover a vanished stash with `git checkout`.
They are right, and that conflict would land mid-panic, which is the worst time
to make anyone choose. So here is the resolution, in one place.

  NEVER, in your own worktree:
    git stash
    git reset
    git reset --hard
    git checkout --
    git checkout <branch>
    git restore
    git clean

  ALWAYS ALLOWED, in your own worktree:
    git status
    git diff
    git log
    git add <specific file>
    git commit
    git push
    git checkout <commit-sha> -- <specific file you want back>

That last one is the recovery command, and it is allowed because it NAMES its
target. It restores one file, or a named set, from a known commit, and touches
nothing else. The banned list is exactly the commands that discard work without
naming a target.

If you need a clean tree for a measurement, COPY the repo to a temp folder and
work there. Never make your own worktree clean.

RULE 2. COMMIT EARLY AND OFTEN.

Commit the moment you have work you would miss. Then keep committing. An
uncommitted measurement is one reset away from gone.

RULE 3. TO GET A CLEAN BASELINE, COPY THE REPO.

Do NOT reset your own worktree to produce a baseline. Instead:

  Copy your repo to a temp folder.
  In the COPY, check out the base commit.
  Run the suite in the COPY.

The copy can be destroyed freely. Your worktree cannot.

RULE 4. A BASELINE IS THE UNMODIFIED TREE.

The FIRST run, before you edited anything, is your baseline.
Every run AFTER an edit is a result.

One agent measured 47.32s after converting its fixtures and called that the
baseline. It was the result. It then compared its unchanged 110.45s baseline
against it and drew the opposite conclusion. Check the order before you compare.

RULE 5. IF A COMMAND PRINTS SUCCESS, VERIFY THE EFFECT.

Read the result back before you rely on it. git stash printed success and was
not true. A message on stdout is a claim about the world, not the world.

IF YOU LOSE WORK

Tell the orchestrator IMMEDIATELY. Do not keep working as though nothing
happened. Say which files you lost and roughly when.

The work is usually recoverable. A stash commit that vanished from the ref list
still exists as an unreachable object and can be restored with git checkout.
That recovery takes minutes, but only if you say something straight away.

IDENTITY RULE

Use your OWN agent name in every heartbeat:

  harness
  core
  features-a
  features-b

One agent sent a heartbeat claiming to be AGENT: harness. Two agents sending the
same identity makes the status board lie about how many agents are running.

If a message names you as a different agent, ignore the name in the message and
use your own name in your reply.