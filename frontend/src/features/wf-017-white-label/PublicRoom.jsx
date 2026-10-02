import { useMemo } from 'react'
import { Badge, Card, ErrorNote, Spinner, useAsync } from '@/components/ui'
import { whiteLabelApi } from './api'
import { Glyph } from './primitives'

/**
 * The buyer-facing view a share link lands on.
 *
 * This is the other half of WF-017. A room's link is
 * ``<custom domain or default host>/r/<Room-Name>-<secret>``, and this component
 * is what serves it. The secret in the path is the room's identity, so the same
 * component works unchanged on the default host and on any custom domain --
 * which is what means changing a domain never breaks a link that was already
 * sent, and is the one behaviour here a buyer is most likely to notice working.
 *
 * Brand tokens are applied as inline custom properties on this component's own
 * wrapper rather than injected as a stylesheet. The server validates them, and a
 * scoped custom property is the narrowest thing that can carry them: two colours
 * and two font stacks, confined to this element, with no stylesheet to escape
 * from and nothing written to `document.head`. A value that is not a string is
 * dropped rather than coerced, so a payload that surprises us cannot become a
 * style attribute.
 */

const TOKEN_ALIASES = {
  primary: '--wl-primary',
  accent: '--wl-accent',
  heading_font: '--wl-heading-font',
  body_font: '--wl-body-font',
}

/**
 * Brand tokens as a style object.
 *
 * The claim that "the server has already refused anything that could break out of
 * a style attribute" is *not* quite true, and the difference matters, so it is
 * stated rather than assumed. `DomainService.update_branding` validates colour
 * and font grammar -- but it is not the only writer of `records.data.branding`.
 * The generic `PATCH /api/records/room/{id}` route writes it with no validation
 * at all, which is the same back door the research's non-removable-secret rule
 * has, and it is reported as a finding in the backend module's docstring. So
 * this function cannot lean on the server; it has to hold the line itself.
 *
 * What it does: it refuses to render any value that is not a string, and it
 * refuses a string that is empty or only whitespace. React sets a custom
 * property through the style object, so nothing here becomes a stylesheet and
 * nothing is written to `document.head` -- which is why an unvalidated value that
 * does slip through is inert rather than dangerous. Dropping rather than
 * coercing is the point: `String({})` is `"[object Object]"`, and turning a
 * surprise into a style attribute is how a surprise becomes a rendering.
 */
function brandStyle(branding) {
  const style = {}
  for (const [key, variable] of Object.entries(TOKEN_ALIASES)) {
    const value = branding?.[key]
    if (typeof value === 'string' && value.trim()) style[variable] = value.trim()
  }
  return style
}

export default function PublicRoom({ path }) {

  // `window.location.host` is sent so the server can say whether this link was
  // opened on the room's own domain or on the default one. Both are correct
  // answers; the badge differs, the room does not.
  const room = useAsync(() => whiteLabelApi.resolveLink(path, window.location.host), [path])
  const state = {
    data: room.data,
    error: room.error,
    loading: room.loading,
  }

  const branding = state.data?.white_label?.branding
  const style = useMemo(() => brandStyle(branding), [branding])

  if (state.loading) {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <Spinner label="Opening room" />
      </div>
    )
  }

  // A 200 that carries nothing renderable is treated exactly like a failure.
  // The API is schema-flexible, so a well-formed response can legitimately have
  // no room in it, and without this the render below would destructure undefined
  // and take down the page a buyer is trying to open.
  if (state.error || !state.data?.white_label) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        {/* Rendered only when there is an error to show. `ErrorNote` stringifies
            whatever it is given, so handing it a null error would print the
            literal word "null" to a buyer under a heading that says the load
            failed. */}
        {state.error && <ErrorNote error={state.error} />}
        <p className="mt-4 text-sm text-muted-foreground">
          This link may have been shared before it finished setting up, or the domain may have
          moved. Ask for the link again rather than editing it by hand — the security code at the
          end of the URL is what identifies the room.
        </p>
        {!state.error && (
          <p className="mt-2 text-sm text-foreground">This link did not resolve to a room.</p>
        )}
      </div>
    )
  }

  const { name, served_on_custom_domain: onCustomDomain } = state.data
  const whiteLabel = state.data.white_label

  return (
    <div className="min-h-screen" style={style}>
      <div className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <p className="font-mono text-sm font-semibold text-foreground">{name}</p>
          {onCustomDomain ? (
            <Badge tone="insert">
              <span className="inline-flex items-center gap-1.5">
                <Glyph name="globe" size={13} />
                {whiteLabel.domain}
              </span>
            </Badge>
          ) : (
            <Badge tone="neutral">
              <span className="inline-flex items-center gap-1.5">
                <Glyph name="shield" size={13} />
                Link verified
              </span>
            </Badge>
          )}
        </div>

        <Card>
          <h1
            className="font-mono text-2xl font-semibold"
            style={{ fontFamily: 'var(--wl-heading-font, inherit)' }}
          >
            {name}
          </h1>
          <p
            className="mt-3 text-sm text-muted-foreground"
            style={{ fontFamily: 'var(--wl-body-font, inherit)' }}
          >
            You have been given access to this room. Everything here is for your review.
          </p>
        </Card>

        <p className="mt-6 flex items-start gap-2 text-xs text-muted-foreground">
          <Glyph name="shield" size={14} className="mt-0.5 shrink-0" />
          <span>
            This address works on the default host and on any domain configured for the room, so a
            link stays usable if the domain later changes.
          </span>
        </p>
      </div>
    </div>
  )
}
