import { useEffect, useId, useRef } from 'react'
import { Icon } from '@/components/ui'

/**
 * `Modal`: the one shared UI primitive this feature needs that
 * `components/ui.jsx` does not carry.
 *
 * `docs/FEATURE-CONTRACT.md` lists `Modal` among the primitives a feature may
 * use, and this copy of `ui.jsx` does not yet define it. The branch appended it
 * to `ui.jsx` along with nine icon paths; both edits are refused, because
 * `ui.jsx` is shared and a hundred features each appending to it is the exact
 * collision the plugin host exists to prevent. So the dialog lives here and is
 * reported as a promotion candidate: any other feature that needs a dialog
 * should have the integrator move this one into `ui.jsx` once, as platform work.
 *
 * Built on the native `<dialog>` element on purpose. `showModal()` gives focus
 * trapping, Escape, inertness of the page behind, and the top layer, which is
 * the accessible behaviour a hand-rolled overlay usually gets wrong. The dimmed
 * blurred background is the generated `::backdrop` style.
 *
 * It meets the same floor as the shared primitives: the close control is a
 * 44x44 minimum target, focus is visible because the browser moves it into the
 * dialog, and every icon sits beside a text label (`sr-only` counts, because the
 * control is icon-only by design and the label is what a screen reader reads).
 */
export default function Modal({ title, description, onClose, children, footer }) {
  const ref = useRef(null)
  // A stable id so the heading labels the dialog, generated once per mount.
  // `useId` rather than `Math.random()` in a ref: the id is read during render,
  // and a ref's current value is not meant to be. It is also pure -- the
  // compiler flagged `Math.random()` in render, and it was a real problem:
  // StrictMode renders twice, so the id the DOM was built from and the id a
  // re-render produced could differ and break the aria-labelledby pairing.
  const generatedTitleId = useId()
  const titleId = `wf011-modal-title-${generatedTitleId}`

  useEffect(() => {
    const node = ref.current
    if (!node) return undefined
    if (typeof node.showModal === 'function') node.showModal()
    else node.setAttribute('open', '')
    return () => {
      if (typeof node.close === 'function' && node.open) node.close()
    }
  }, [])

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId.current}
      onCancel={(event) => {
        // Route Escape through the caller's handler so unmounting stays in one
        // place and the dialog never closes behind a still-rendered page.
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        // A click that lands on the dialog element itself is a backdrop click.
        if (event.target === event.currentTarget) onClose()
      }}
      className="glass m-auto max-h-[90vh] w-[min(560px,92vw)] overflow-y-auto rounded-2xl
        p-0 text-foreground backdrop:bg-black/60 backdrop:backdrop-blur-sm"
    >
      <div className="flex items-start justify-between gap-4 border-b border-border-subtle/25 p-5">
        <div className="min-w-0">
          <h2 id={titleId.current} className="font-mono text-base font-semibold">
            {title}
          </h2>
          {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg
            text-muted-foreground transition-colors duration-200 hover:bg-muted hover:text-foreground"
        >
          <span className="sr-only">Close</span>
          <Icon name="close" />
        </button>
      </div>

      <div className="space-y-4 p-5">{children}</div>

      {footer && (
        <div className="flex flex-wrap justify-end gap-2 border-t border-border-subtle/25 p-5">
          {footer}
        </div>
      )}
    </dialog>
  )
}
