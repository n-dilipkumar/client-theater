import Glyph from './icons'
import { Note } from './primitives'

/**
 * Renders a placed fragment.
 *
 * The same component draws the editor's canvas preview and the buyer view, so
 * what a collaborator sees while editing is what a member sees once the page is
 * published. That is the whole point of the publish step being explicit, and
 * sharing the renderer is what makes the promise checkable.
 *
 * Honesty about undocumented fields
 * ---------------------------------
 * The research for WF-002 enumerates the configuration fields for some fragments
 * and not for others. Where fields are documented this renders them meaningfully
 * (a Timeline Block really does show numbered steps). Where they are not, it
 * renders the stored values generically and says so, rather than inventing a
 * field vocabulary the vendor never published.
 */

/** `document_1` -> "Document 1", `current_step` -> "Current step". */
export function keyLabel(key) {
  return String(key)
    .replace(/[_-]+/g, ' ')
    .replace(/^./, (c) => c.toUpperCase())
}

function asText(value) {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

/** The config keys a fragment does not declare, i.e. a team's own fields. */
function ownKeys(block) {
  const declared = new Set((block.fields || []).map((field) => field.key))
  return Object.keys(block.config || {}).filter((key) => !declared.has(key))
}

function Shell({ block, children, className = '' }) {
  return (
    <section
      aria-label={block.name || block.fragment}
      className={`rounded-xl border border-border-subtle/30 bg-card/50 p-4 ${className}`}
    >
      {children}
    </section>
  )
}

function Undocumented({ block }) {
  const keys = ownKeys(block)
  if (keys.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No fields configured. The documentation for this fragment does not list any, so add
        whatever the fragment needs.
      </p>
    )
  }
  return (
    <dl className="grid gap-2 sm:grid-cols-2">
      {keys.map((key) => (
        <div key={key} className="min-w-0">
          <dt className="text-xs text-muted-foreground">{keyLabel(key)}</dt>
          <dd className="truncate font-mono text-sm text-foreground">{asText(block.config[key])}</dd>
        </div>
      ))}
    </dl>
  )
}

function Documents({ items, emptyText }) {
  if (!items || items.length === 0) {
    return <p className="text-sm text-muted-foreground">{emptyText}</p>
  }
  return (
    <ul className="grid gap-2 sm:grid-cols-2">
      {items.map((doc) => (
        <li key={doc.id} className="rounded-lg border border-border-subtle/30 bg-background/40 p-3">
          <div className="flex items-start gap-2">
            <span className="mt-0.5 text-accent">
              <Glyph name="page" size={16} />
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-foreground">
                {doc.title || 'Untitled document'}
              </p>
              <p className="font-mono text-xs text-muted-foreground">
                {doc.resolved === false
                  ? 'No longer in this room'
                  : [doc.kind, doc.pages ? `${doc.pages} pages` : null]
                      .filter(Boolean)
                      .join(' · ') || 'Document'}
              </p>
            </div>
          </div>
        </li>
      ))}
    </ul>
  )
}

/**
 * @param block  a block as the buyer view returns it: id, fragment, name,
 *               config, fields, and the resolved `documents` for its selectors.
 * @param room   the room record, for Header Main's archived notice.
 */
export default function BlockRenderer({ block, room, documents }) {
  const config = block.config || {}
  const docs = documents || []

  switch (block.fragment) {
    case 'header-main':
      return (
        <Shell block={block} className="border-accent/30">
          <h2 className="font-mono text-lg font-semibold text-foreground">
            {asText(config.heading) || (room?.data?.name ?? 'Sales room')}
          </h2>
          {asText(config.subheading) && (
            <p className="mt-1 text-sm text-muted-foreground">{asText(config.subheading)}</p>
          )}
          {room?.data?.status === 'archived' && (
            <p className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-sm text-amber-100">
              This digital sales room is archived. New comments cannot be added, and it can no
              longer be shared.
            </p>
          )}
          <Undocumented block={block} />
        </Shell>
      )

    case 'header-user':
      return (
        <Shell block={block}>
          <p className="text-sm text-muted-foreground">Signed-in member</p>
          <Undocumented block={block} />
        </Shell>
      )

    case 'welcome':
      return (
        <Shell block={block}>
          <h3 className="font-mono text-base font-semibold text-foreground">
            {asText(config.heading) || 'Welcome'}
          </h3>
          {asText(config.body) && (
            <p className="mt-1 text-sm text-foreground/90">{asText(config.body)}</p>
          )}
          <Undocumented block={block} />
        </Shell>
      )

    case 'text':
      return (
        <Shell block={block}>
          <p className="text-sm whitespace-pre-line text-foreground/90">
            {asText(config.body) || asText(config.text) || 'Empty text block.'}
          </p>
        </Shell>
      )

    case 'timeline': {
      // Documented: a numbered sequence of steps, each with a secondary line
      // for an estimate. Number of Steps sets how many appear (default four)
      // and Current Step marks how far the deal has progressed.
      const count = Math.max(1, Math.min(Number(config.number_of_steps ?? 4) || 4, 20))
      const current = Math.max(0, Number(config.current_step ?? 0) || 0)
      const steps = Array.from({ length: count }, (_, index) => index + 1)
      return (
        <Shell block={block}>
          <h3 className="font-mono text-sm font-semibold text-foreground">Timeline</h3>
          <ol className="mt-3 space-y-2">
            {steps.map((step) => {
              const done = step <= current
              return (
                <li key={step} className="flex items-center gap-3">
                  <span
                    aria-hidden="true"
                    className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border font-mono text-xs ${
                      done
                        ? 'border-accent bg-accent/20 text-accent'
                        : 'border-border-subtle text-muted-foreground'
                    }`}
                  >
                    {step}
                  </span>
                  <span className="flex-1 text-sm text-foreground">
                    {asText(config[`step_${step}_title`]) || `Step ${step}`}
                    <span className="block text-xs text-muted-foreground">
                      {asText(config[`step_${step}_estimate`]) || (done ? 'Complete' : 'Estimate not set')}
                    </span>
                  </span>
                  {step === current && <span className="font-mono text-xs text-accent">current</span>}
                </li>
              )
            })}
          </ol>
        </Shell>
      )
    }

    case 'video': {
      // Documented: URL points to the video, Width and Height set the player's
      // dimensions, and autoplay is off by default.
      const url = asText(config.url)
      const width = Number(config.width) || undefined
      const height = Number(config.height) || undefined
      return (
        <Shell block={block}>
          {url ? (
            <video
              controls
              autoPlay={Boolean(config.autoplay)}
              width={width}
              height={height}
              preload="metadata"
              className="max-w-full rounded-lg bg-black"
              style={{
                width: width ? `${width}px` : undefined,
                maxHeight: height ? `${height}px` : undefined,
              }}
            >
              <source src={url} />
              Your browser cannot play this video.
            </video>
          ) : (
            <p className="text-sm text-muted-foreground">No video URL set.</p>
          )}
          {config.autoplay ? (
            <p className="mt-2 text-xs text-muted-foreground">Autoplay is on for this block.</p>
          ) : null}
        </Shell>
      )
    }

    case 'document-gallery':
      return (
        <Shell block={block}>
          <h3 className="font-mono text-sm font-semibold text-foreground">Documents</h3>
          <div className="mt-2">
            <Documents items={docs} emptyText="No documents selected for this block." />
          </div>
        </Shell>
      )

    case 'pdf-preview':
      return (
        <Shell block={block}>
          <h3 className="font-mono text-sm font-semibold text-foreground">PDF preview</h3>
          <div className="mt-2">
            <Documents items={docs} emptyText="No PDF selected." />
          </div>
        </Shell>
      )

    case 'gallery':
      return (
        <Shell block={block}>
          <h3 className="font-mono text-sm font-semibold text-foreground">Gallery</h3>
          <Undocumented block={block} />
        </Shell>
      )

    case 'our-team':
      return (
        <Shell block={block}>
          <h3 className="font-mono text-sm font-semibold text-foreground">Your team</h3>
          <Undocumented block={block} />
        </Shell>
      )

    case 'question-and-answer':
      return (
        <Shell block={block}>
          <h3 className="font-mono text-sm font-semibold text-foreground">Questions</h3>
          <Undocumented block={block} />
        </Shell>
      )

    // -- DSR Fragments: console chrome -------------------------------------- //
    // The three documented accessibility hooks are honoured here rather than
    // stored and ignored.
    case 'page-bar':
      return (
        <div
          role="banner"
          aria-label={asText(config.header_image_alt_description) || undefined}
          className="rounded-lg border border-border-subtle/30 bg-background/40 p-3"
        >
          <p className="text-xs text-muted-foreground">
            Page Bar
            {asText(config.header_image_alt_description)
              ? ` · alt text: ${asText(config.header_image_alt_description)}`
              : ' · no alt description set'}
          </p>
        </div>
      )

    case 'sidebar':
      return (
        <aside
          aria-label={asText(config.sidebar_aria_label) || undefined}
          className="rounded-lg border border-border-subtle/30 bg-background/40 p-3"
        >
          <p className="text-xs text-muted-foreground">Sidebar</p>
        </aside>
      )

    case 'sidebar-trigger':
      return (
        <div className="rounded-lg border border-border-subtle/30 bg-background/40 p-3">
          <p className="text-xs text-muted-foreground">Sidebar trigger</p>
        </div>
      )

    case 'vertical-navigation':
      return (
        <nav
          aria-label={asText(config.aria_label) || undefined}
          className="rounded-lg border border-border-subtle/30 bg-background/40 p-3"
        >
          <p className="text-xs text-muted-foreground">Vertical navigation</p>
        </nav>
      )

    // -- Analytics set, and a team's own set -------------------------------- //
    default: {
      if (block.set === 'digital-sales-room-analytics') {
        return (
          <Shell block={block}>
            <div className="flex items-center gap-2">
              <span className="text-accent">
                <Glyph name={block.icon || 'engagement-chart'} size={16} />
              </span>
              <h3 className="font-mono text-sm font-semibold text-foreground">
                {block.name || block.fragment}
              </h3>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              Engagement data for this room. Widgets are supplied by the analytics pipeline.
            </p>
            <div className="mt-2">
              <Undocumented block={block} />
            </div>
          </Shell>
        )
      }
      if (block.known === false) {
        return (
          <Shell block={block}>
            <Note tone="warn" title="Fragment not registered">
              <code className="font-mono">{block.fragment}</code> is not in the catalogue any more,
              so there is nothing to render. The stored configuration is still published
              unchanged.
            </Note>
          </Shell>
        )
      }
      return (
        <Shell block={block}>
          <h3 className="font-mono text-sm font-semibold text-foreground">
            {block.name || block.fragment}
          </h3>
          <div className="mt-2">
            <Undocumented block={block} />
          </div>
        </Shell>
      )
    }
  }
}
