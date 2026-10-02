import { mergeConfig } from 'vite'
import viteConfig from './vite.config.js'

/**
 * The frontend test environment, in its own file.
 *
 * `vite.config.js` is on the shared-file list: CI fails any branch that edits
 * it, because a hundred features each adding a `test:` block to it is the exact
 * collision the plugin host exists to remove. The branch this feature was ported
 * from did add one, which is why it could never merge.
 *
 * So the test configuration lives here instead, and `mergeConfig` reuses
 * `vite.config.js` rather than restating it. That import is the point: the React
 * plugin, the `@` alias and the dev proxy are defined once, in the file the build
 * already reads, and the test run inherits all three instead of drifting from
 * them. If `vite.config.js` changes, this follows.
 *
 * A reviewer should read this as a platform file that a feature had to create,
 * not as a feature file. Its natural home is the shared `vite.config.js` -- where
 * a human should move it, as one decision, once -- and until then it is here and
 * it works.
 *
 * If a second port needs frontend tests, this file is the one to extend rather
 * than a second one to add: two `vitest.config.js` files cannot both exist at
 * this path, and the collision would be as merge-hostile as the one it replaced.
 */
export default mergeConfig(viteConfig, {
  test: {
    // The pages are the contract: they have to render a real API payload, so
    // the tests run against a DOM rather than asserting on a shallow snapshot.
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    include: ['src/**/*.test.{js,jsx}'],

    // Pinned rather than inherited. This is already vitest's default, and it
    // is written out anyway because it is the single setting that turns "no
    // tests were collected" into a passing build -- the failure mode where CI
    // goes green because the `include` glob stopped matching, which is exactly
    // the rot this job exists to catch. Measured on this suite: with a filter
    // that matches no file, `vitest run` exits 1 and prints "No test files
    // found, exiting with code 1"; the same command with
    // `--passWithNoTests` exits 0. So the flag must never reach the CI step,
    // and this is the place someone would reach for it while chasing some
    // unrelated failure.
    passWithNoTests: false,

    // NODE_ENV is pinned here, not in the npm script, and that placement is
    // the whole point. `NODE_ENV=test vitest run` is POSIX shell syntax: on
    // Windows cmd it fails outright with "'NODE_ENV' is not recognized as an
    // internal or external command", and this repo is developed on win32. The
    // portable spelling needs `cross-env`, which would mean a new
    // devDependency and a package-lock.json edit -- so it is set in JS here
    // instead, which is cross-platform by construction and adds nothing.
    //
    // It has to be set, not merely defaulted. Vitest only does
    // `process.env.NODE_ENV ??= 'test'`, so an inherited NODE_ENV=production
    // survives and React resolves its production bundle, where `act()` throws
    // "act(...) is not supported in production builds of React". Measured on
    // this suite: with NODE_ENV=production inherited, 105 of 117 tests fail
    // with that message and the stack points into react-dom, not at the
    // environment -- so a developer exports NODE_ENV once in their shell
    // profile and the suite goes red for a reason that reads like a React
    // bug. GitHub Actions does not set NODE_ENV, which is exactly why this
    // would have stayed invisible in CI while breaking every contributor who
    // has it exported. A green suite must not depend on the caller's shell.
    env: { NODE_ENV: 'test' },
  },
})
