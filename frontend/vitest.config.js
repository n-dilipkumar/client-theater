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
export default mergeConfig(
  viteConfig,
  {
    test: {
      // The pages are the contract: they have to render a real API payload, so
      // the tests run against a DOM rather than asserting on a shallow snapshot.
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.js'],
      include: ['src/**/*.test.{js,jsx}'],
    },
  },
)
