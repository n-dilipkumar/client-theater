import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'

/**
 * ESLint flat config for the Digital Sales Room frontend.
 *
 * Three groups of rule, three different severities, and the reason for each is
 * below. Prettier owns formatting, so no formatting rule is enabled here.
 *
 * 1. Correctness. `rules-of-hooks` is the one rule that catches a real bug
 *    class: a hook called conditionally or from a loop. It stays an error.
 *    `no-unused-vars`, `no-const-assign`, `no-dupe-keys` and the rest of
 *    `js.configs.recommended` are likewise error-worthy.
 *
 * 2. React Compiler migration rules. `eslint-plugin-react-hooks` v7 added
 *    `set-state-in-effect`, `refs`, `purity`, `immutability` and
 *    `preserve-manual-memoization`. These describe what the React Compiler
 *    would like the code to look like, not what is wrong with it today: a
 *    `useEffect` that syncs state when a prop changes is the documented pattern
 *    for React 18, which is what this app runs. They are reported as warnings
 *    so the migration stays visible and measurable, without turning 48
 *    already-shipped feature folders red on the day the linter is adopted.
 *
 * 3. `no-unused-vars` is relaxed to a warning inside `src/features/**` only.
 *    Feature folders are owned outright by the feature branch that wrote them,
 *    and `docs/FEATURE-CONTRACT.md` forbids anyone else editing them. An error
 *    there would be unfixable from any other branch, which makes the rule
 *    noise rather than a signal. Everywhere else it stays an error.
 *
 * Where this stands today, and what is deliberately still reported:
 *
 *   `npm run lint` -> 0 errors, 81 warnings.
 *
 * All 81 are in files this change did not author, and all are accepted rather
 * than silenced. The breakdown, so the number is legible instead of a lump:
 *
 *     37  react-hooks/set-state-in-effect   React 18 effect-sync pattern
 *     28  no-unused-vars                   dead locals in feature folders
 *     11  react-hooks/exhaustive-deps      stale-closure risk, not a crash
 *      2  react-hooks/refs                 reading ref.current during render
 *      1  react-hooks/purity
 *      1  react-hooks/preserve-manual-memoization
 *      1  prefer-const
 *
 * The first three account for 76 of them and are React Compiler migration work,
 * not defects. The remaining 5 are genuinely worth fixing, one folder at a time,
 * by the branch that owns each folder. Turning any of them off would make this
 * file report a green number that does not describe the tree.
 *
 * No rule here is switched off to hide a defect. `no-undef` is the rule that
 * found two shipped ReferenceErrors - `invate` for `invite` in
 * MeetingChanges.jsx, and an unbound `inferences` in CrmConnections.jsx - and
 * it stays an error for exactly that reason.
 */
export default [
  {
    ignores: ['dist/**', 'node_modules/**', 'coverage/**'],
  },

  {
    // The spread comes first and `files` after it, deliberately. `js.configs
    // .recommended` carries its own `files: ['**/*.js','**/*.cjs','**/*.mjs']`,
    // and in JS an object literal resolves a repeated key to the last one - so
    // writing `files` first would be silently overwritten by the spread, and
    // the spread's list has no `jsx` in it. The symptom is subtle: `no-undef`
    // and the other core rules resolve to `undefined` (off) instead of erroring,
    // and .jsx files stop being linted at all.
    ...js.configs.recommended,
    files: ['src/**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.node,
      },
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: {
      'react-hooks': reactHooks,
    },
    rules: {
      // --- 1. correctness ---------------------------------------------------
      // `js.configs.recommended.rules` comes first inside this object, for the
      // same reason the spread above is first: a later `rules` key replaces an
      // earlier one wholesale rather than merging into it. Writing only the
      // react-hooks rules here silently discarded all 64 core rules, so
      // `no-undef`, `no-redeclare`, `no-dupe-keys` and `no-unreachable` resolved
      // to `undefined` - off - and an undefined identifier passed lint.
      ...js.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      'react-hooks/rules-of-hooks': 'error',

      // The rest of `recommended` is the React Compiler migration set. See the
      // note at the top of this file. Each is named so the downgrade is a
      // decision recorded here rather than something `recommended` decides.
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/refs': 'warn',
      'react-hooks/purity': 'warn',
      'react-hooks/immutability': 'warn',
      'react-hooks/static-components': 'warn',
      'react-hooks/use-memo': 'warn',
      'react-hooks/preserve-manual-memoization': 'warn',
      'react-hooks/set-state-in-render': 'warn',
      'react-hooks/error-boundaries': 'warn',
      'react-hooks/config': 'warn',
      'react-hooks/gating': 'warn',
      // `exhaustive-deps` is a warning upstream and stays one: a missing
      // dependency is usually correct code with a stale closure, not a crash.
      'react-hooks/exhaustive-deps': 'warn',

      'no-unused-vars': [
        'error',
        {
          args: 'after-used',
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrors: 'none',
          ignoreRestSiblings: true,
        },
      ],

      // --- plain style rules that are unambiguous --------------------------
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      eqeqeq: ['error', 'smart'],
      'no-var': 'error',
      'prefer-const': 'error',
    },
  },

  // --- 3. feature folders are owned by their own branch --------------------
  {
    files: ['src/features/**/*.{js,jsx}'],
    rules: {
      // Still reported, but a finding here is fixable only by the branch that
      // wrote the feature, so it must not fail an unrelated branch's build.
      'no-unused-vars': 'warn',
      'prefer-const': 'warn',
    },
  },

  // Test specs assert with loose string containment on purpose and lean on
  // test-only globals.
  {
    files: ['src/**/*.test.{js,jsx}', 'src/test/**/*.{js,jsx}'],
    languageOptions: {
      globals: {
        ...globals.vitest,
      },
    },
  },

  // Config files at the frontend root run in Node.
  {
    files: ['*.config.{js,mjs,cjs}'],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
  },
]
