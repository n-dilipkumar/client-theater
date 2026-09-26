import { useId, useRef, useState } from 'react'
import { Icon, inputClass } from './ui'

/**
 * The *Email Addresses* field.
 *
 * The documented interaction is "type an address and press Enter or type a
 * comma", so both keys commit the current token, and pasting a
 * comma-separated list is split the same way. The committed addresses become
 * chips with their own remove buttons rather than a comma-joined string,
 * because the person needs to see and undo each one before sending.
 *
 * Validation is deliberately forgiving about case and surrounding space and
 * strict about shape: a chip that is not an address never gets committed, and
 * the reason is shown next to the field rather than raised at the server.
 */

const EMAIL_RE = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/

/** Split pasted or typed text on the separators the docs name, plus semicolon. */
function splitTokens(text) {
  return String(text)
    .split(/[,;\n]+/)
    .map((token) => token.trim())
    .filter(Boolean)
}

export function EmailChips({ value, onChange, disabled = false, id, describedBy }) {
  const [draft, setDraft] = useState('')
  const [problem, setProblem] = useState(null)
  const inputRef = useRef(null)
  const listId = useId()

  function commit(tokens) {
    const accepted = []
    const rejected = []

    for (const token of tokens) {
      const email = token.toLowerCase()
      if (!EMAIL_RE.test(email) || email.includes('..')) {
        rejected.push(token)
      } else if (!value.includes(email) && !accepted.includes(email)) {
        accepted.push(email)
      }
    }

    if (accepted.length) {
      onChange([...value, ...accepted])
      setDraft('')
    }
    // An address already on the list is a no-op, not a problem worth showing.
    setProblem(rejected.length ? `"${rejected[0]}" is not a valid email address` : null)
  }

  function onKeyDown(event) {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault()
      commit(splitTokens(draft))
      return
    }
    if (event.key === 'Backspace' && !draft && value.length) {
      onChange(value.slice(0, -1))
    }
  }

  function remove(email) {
    onChange(value.filter((item) => item !== email))
    // Keep the caret in the field: removing a chip should not jump focus to
    // the top of the dialog.
    inputRef.current?.focus()
  }

  return (
    <div className="flex flex-col gap-1.5">
      {/*
        A listbox pattern: the input owns the caret, the chips are announced as
        a list, and each chip's remove button is a real button with a name.
      */}
      <div
        className={`flex min-h-11 flex-wrap items-center gap-1.5 rounded-lg border border-border-subtle/50 bg-background/60 px-2 py-1.5 transition-colors duration-200 focus-within:border-accent ${
          problem ? 'border-destructive' : ''
        }`}
      >
        {value.map((email) => (
          <span
            key={email}
            className="inline-flex items-center gap-1 rounded-md border border-accent/30 bg-accent/10 py-0.5 pl-2 pr-0.5 font-mono text-xs text-foreground"
          >
            {email}
            <button
              type="button"
              onClick={() => remove(email)}
              disabled={disabled}
              className="-my-2.5 inline-flex min-h-11 min-w-11 items-center justify-center rounded-md text-muted-foreground transition-colors duration-150 hover:bg-destructive/20 hover:text-destructive disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Icon name="close" size={14} />
              <span className="sr-only">Remove {email}</span>
            </button>
          </span>
        ))}

        <input
          ref={inputRef}
          id={id}
          type="text"
          value={draft}
          disabled={disabled}
          onChange={(event) => {
            const text = event.target.value
            // A comma or semicolon commits everything typed so far, which is
            // the documented behaviour, and leaves an empty box behind.
            if (/[,;]/.test(text)) {
              commit(splitTokens(text))
              return
            }
            setDraft(text)
            if (problem) setProblem(null)
          }}
          onKeyDown={onKeyDown}
          onBlur={() => commit(splitTokens(draft))}
          onPaste={(event) => {
            const text = event.clipboardData.getData('text')
            if (/[,;\n]/.test(text)) {
              event.preventDefault()
              commit(splitTokens(text))
            }
          }}
          aria-describedby={describedBy}
          aria-invalid={problem ? 'true' : undefined}
          aria-controls={listId}
          className="min-h-9 min-w-40 flex-1 bg-transparent px-1 text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none disabled:cursor-not-allowed"
          placeholder={value.length ? 'Add another address' : 'buyer@company.com'}
          autoComplete="off"
          spellCheck="false"
        />
      </div>

      <ul id={listId} className="sr-only">
        {value.map((email) => (
          <li key={email}>{email}</li>
        ))}
      </ul>

      <p className={`text-xs ${problem ? 'text-destructive' : 'text-muted-foreground/80'}`} role={problem ? 'alert' : undefined}>
        {problem || 'Press Enter or a comma after each address.'}
      </p>
    </div>
  )
}
