import { useEffect, useState } from 'react'
import { Field, inputClass } from '@/components/ui'

/* Demo gate. NOT authentication.
 *
 * Any non-empty username and password is accepted. Nothing is verified, no
 * request leaves the browser, and the only thing kept is a display name in
 * sessionStorage so a refresh does not bounce you back to this screen.
 *
 * It exists to gate the product behind a front door and to give the shell a
 * signed-in identity to render. Treat it as a placeholder until a real
 * identity provider exists, and do not describe it to anyone as a login. */

const STORAGE_KEY = 'client-theater:operator'

export function readOperator() {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function writeOperator(name) {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ name }))
  } catch {
    /* Private browsing can refuse writes. The session still works in memory. */
  }
}

export function clearOperator() {
  try {
    window.sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    /* Nothing to clean up if storage was never writable. */
  }
}

export function Login({ onSignedIn }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    document.title = 'Sign in · Client Theater'
  }, [])

  function submit(event) {
    event.preventDefault()
    if (!username.trim()) {
      setError('Enter a name so the audit log has an actor.')
      return
    }
    if (!password) {
      setError('Enter any password.')
      return
    }
    writeOperator(username.trim())
    onSignedIn({ name: username.trim() })
  }

  return (
    <div className="flex min-h-[100dvh] flex-col bg-background">
      <div className="flex flex-1 items-center justify-center px-6 py-16">
        <div className="grid w-full max-w-4xl grid-cols-1 items-center gap-14 lg:grid-cols-2">
          <div className="hidden lg:block">
            <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-accent">
              Digital sales rooms
            </p>
            <h1 className="mt-5 max-w-[14ch] text-[2.5rem] leading-[1.06] font-semibold tracking-[-0.03em]">
              One room for the whole buying committee.
            </h1>
            <p className="mt-5 max-w-[46ch] text-[15px] leading-relaxed text-muted-foreground">
              Content, a mutual action plan, and procurement detail for one deal, tracked while the
              committee works between your calls.
            </p>
            <dl className="mt-10 grid grid-cols-1 gap-4 border-t border-border-subtle pt-6 sm:grid-cols-3">
              {[
                ['Mutual action plan', 'one dated spine'],
                ['Committee map', 'who has read it'],
                ['Audit log', 'every write, same txn'],
              ].map(([term, detail]) => (
                <div key={term}>
                  <dt className="text-[13px] font-medium">{term}</dt>
                  <dd className="mt-0.5 text-[13px] text-muted-foreground">{detail}</dd>
                </div>
              ))}
            </dl>
          </div>

          <div className="w-full border border-border-subtle bg-surface p-8">
            <h2 className="text-[1.35rem] font-semibold">Sign in</h2>
            <p className="mt-2 text-[14px] text-muted-foreground">
              Any name and any password will open the workspace.
            </p>

            <form onSubmit={submit} noValidate className="mt-7 flex flex-col gap-5">
              <Field label="Your name" id="login-username" hint="Shown as the actor on audit rows.">
                <input
                  id="login-username"
                  name="username"
                  value={username}
                  onChange={(event) => {
                    setUsername(event.target.value)
                    if (error) setError('')
                  }}
                  autoComplete="username"
                  placeholder="m.osei"
                  className={inputClass}
                  aria-invalid={Boolean(error)}
                />
              </Field>

              <Field label="Password" id="login-password">
                <input
                  id="login-password"
                  name="password"
                  type="password"
                  value={password}
                  onChange={(event) => {
                    setPassword(event.target.value)
                    if (error) setError('')
                  }}
                  autoComplete="current-password"
                  placeholder="Anything"
                  className={inputClass}
                  aria-invalid={Boolean(error)}
                />
              </Field>

              <p
                id="login-error"
                role={error ? 'alert' : undefined}
                className={error ? 'text-[13px] text-destructive' : 'text-[13px] text-muted-foreground'}
              >
                {error || 'Demo gate. Nothing is verified and no data leaves the browser.'}
              </p>

              <button
                type="submit"
                className="inline-flex min-h-11 items-center justify-center rounded-sm bg-accent px-6 text-[15px] font-medium text-on-accent transition-colors duration-150 hover:bg-primary active:translate-y-[1px]"
              >
                Open the workspace
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  )
}
