import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeAll, vi } from 'vitest'

/**
 * The shared frontend test setup, and the only place in the suite that can undo
 * what a test file does to the world outside its own module registry.
 *
 * Why this file has to exist
 * --------------------------
 * `vitest` rebuilds the module registry for every test file. It does not rebuild
 * the global object. Both halves were measured on this suite rather than
 * assumed, with two throwaway probe files run in one process in a known order:
 *
 *   file A: mutates a module-scope `let` in an imported module, and installs a
 *           global by plain assignment
 *   file D: reads both back
 *     MODULE_SLOT   = "pristine"   <- module state does not cross files
 *     GLOBAL_FETCH  = "A"          <- the global does
 *
 * So a test file that writes `globalThis.fetch = ...` is writing to an object the
 * next file in the same worker reads. Nineteen files install `fetch` that way,
 * two of them shared helpers with eight consumers between them, and one of them
 * assigns `undefined`, which takes `fetch` away from whichever file runs next in
 * that worker.
 *
 * Why `vi.restoreAllMocks()` was not enough
 * -----------------------------------------
 * It restores spies and mock implementations. It does not touch a property that
 * was assigned outright, because it never recorded one. `vi.unstubAllGlobals()`
 * is the call that undoes a `vi.stubGlobal`, so it is called here too -- but
 * seven files stub `fetch` that way and nineteen assign it, and only the second
 * group needs the mechanism below.
 *
 * The mechanism, and why it is not one line
 * -----------------------------------------
 * The tempting version is `afterEach(() => vi.unstubAllGlobals())`. It does not
 * reach a plain assignment, so on this suite it would fix nothing, and that was
 * measured rather than argued: a probe that dirties `fetch` four ways survived it
 * completely. Capturing the original value is the part that matters, and two
 * further things are load-bearing:
 *
 * 1. The pristine value has to be captured *once per worker*. The setup file is
 *    itself re-evaluated for every test file, so a snapshot taken at its module
 *    scope records whatever the previous file left behind and then faithfully
 *    restores the leak -- a per-file snapshot provably fixes nothing. The only
 *    thing that crosses a file boundary in a worker is the global object, which
 *    is why the snapshot is stashed on a symbol on `globalThis`.
 *
 * 2. Restoring after every test alone would destroy a global a file installs at
 *    module load, because module load happens before the file's first
 *    `beforeEach` and so looks exactly like a test's leftover by the time
 *    `afterEach` runs. Hence two floors: the worker's pristine one, restored
 *    once per file before that file's body runs, and the file's own, captured
 *    after the body has run and restored after every test.
 *
 * Why the whole global surface and not a list
 * --------------------------------------------
 * A list of `fetch` and `localStorage` has to be extended by whoever writes the
 * hundredth feature that stubs something, and the failure mode of a list is a
 * suite that is quietly order-dependent again. So the floor is the whole global.
 * That is only safe because it was measured first: between two test files in one
 * worker, the jsdom global is the *same object* (`window` identical by
 * reference), the environment's own properties are accessors whose descriptors
 * do not move, and the only own key the environment ever removes is none -- the
 * only things that change are the ones a test changed. An exclusion list for the
 * environment's surface was written, measured to be unnecessary, and deleted.
 *
 * The counters on the worker state are read by the throwaway probes that
 * verify this file. `unfixable` is the interesting one: on this suite exactly one
 * key cannot be restored -- `__vitest_index__`, which vitest defines as
 * non-configurable and rewrites for every file. It is counted rather than thrown
 * on, because one key the runner owns is not a reason to fail a test that has
 * nothing to do with it, and counting it is what keeps that honest.
 */

const WORKER_STATE = Symbol.for('digital-sales-room.test.workerState')

/** Descriptors of every own property of `globalThis`, symbols included. */
function snapshotDescriptors() {
  return Object.getOwnPropertyDescriptors(globalThis)
}

/** Do two descriptors hold the same thing? Accessors are compared by identity. */
function holdsSameValue(current, wanted) {
  if ('value' in current && 'value' in wanted) return current.value === wanted.value
  if (!('value' in current) && !('value' in wanted)) {
    return current.get === wanted.get && current.set === wanted.set
  }
  return false
}

/**
 * Put `globalThis` back to `floor`, and report what it could not.
 *
 * A key the floor did not have is deleted, which is how a global a test added
 * outright gets removed. A key the floor had is redefined to the floor's
 * descriptor.
 */
function restoreGlobals(floor, report) {
  for (const key of Reflect.ownKeys(globalThis)) {
    if (key === WORKER_STATE) continue

    const wanted = floor[key]
    const current = Object.getOwnPropertyDescriptor(globalThis, key)

    if (wanted === undefined) {
      if (current === undefined) continue
      if (current.configurable) {
        delete globalThis[key]
        report.deleted += 1
      } else {
        report.unfixable += 1
      }
      continue
    }

    if (current && holdsSameValue(current, wanted)) continue

    if (!wanted.configurable) {
      report.unfixable += 1
      continue
    }
    Object.defineProperty(globalThis, key, wanted)
    report.restored += 1
  }
}

function snapshotStorages() {
  const contents = new Map()
  for (const name of ['localStorage', 'sessionStorage']) {
    const store = globalThis[name]
    if (!store) continue
    const entries = []
    for (let index = 0; index < store.length; index += 1) {
      const key = store.key(index)
      entries.push([key, store.getItem(key)])
    }
    contents.set(name, entries)
  }
  return contents
}

/**
 * Put both storages back to `contents`.
 *
 * These are accessors on `globalThis` rather than data properties, so the
 * descriptor restore above does nothing for them and the contents need restoring
 * separately. Cleared rather than diffed: the alternative is an enumerating walk
 * over a store a test may have grown, for a store whose whole contents are the
 * thing being restored.
 */
function restoreStorage(contents) {
  for (const [name, entries] of contents) {
    const store = globalThis[name]
    if (!store) continue
    store.clear()
    for (const [key, value] of entries) store.setItem(key, value)
  }
}

/** The worker's pristine state: taken once, carried across files on a symbol. */
function workerState() {
  const existing = globalThis[WORKER_STATE]
  if (existing) return existing

  const state = {
    floor: snapshotDescriptors(),
    storage: snapshotStorages(),
    restored: 0,
    deleted: 0,
    unfixable: 0,
  }
  Object.defineProperty(globalThis, WORKER_STATE, {
    value: state,
    writable: true,
    enumerable: false,
    configurable: true,
  })
  return state
}

const worker = workerState()

/**
 * This file's floor, which starts as the worker's and is re-taken in `beforeAll`
 * once the test file's own module body has run.
 */
const floor = { descriptors: worker.floor, storage: worker.storage }

function restoreToFloor() {
  restoreGlobals(floor.descriptors, worker)
  restoreStorage(floor.storage)
}

// Runs here, before the test file's module body, so a file that inherits a
// leftover does not keep it and a file that installs a global at module load
// still has it for its own tests. For the first file in a worker this is a no-op.
restoreToFloor()

beforeAll(() => {
  floor.descriptors = snapshotDescriptors()
  floor.storage = snapshotStorages()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  // `unstubAllGlobals` first and the floor on top of it. The order is
  // load-bearing: `vi.stubGlobal` records the descriptor of a key only the first
  // time it stubs that key (vitest 2.1), so a key a previous file poisoned and
  // never unstubbed has the *poison* recorded as its original. Unstubbing puts
  // the poison back, and only a restore afterwards clears it.
  vi.unstubAllGlobals()
  restoreToFloor()
})

// jsdom has no clipboard, and the copy button has to survive that without
// throwing. Stubbed rather than skipped so the button's own logic is exercised.
// Installed after the floor above, so `beforeAll` snapshots it as part of this
// file's own state and it is not undone between this file's tests.
if (!navigator.clipboard) {
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  })
}
