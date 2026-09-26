import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { Badge, Card, ErrorNote, Icon, Spinner } from '../components/ui'

/**
 * The buyer-facing view a share link lands on.
 *
 * This is the other half of WF-017. A room's link is
 * ``<custom domain or default host>/r/<Room-Name>-<secret>``, and this component
 * is what serves it. The secret in the path is the room's identity, so the same
 * component works unchanged on the default host and on any custom domain --
 * which is what means changing a domain never breaks a link that was already
 * sent.
 *
 * Brand tokens are applied as inline custom properties on the wrapper rather
 * than injected as a stylesheet. The server validates them, and this is the
 * narrowest thing that can carry them: two colours and two font stacks, scoped
 * to this element, with no stylesheet to escape into.
 */

const TOKEN_ALIASES = {
  primary: '--wl-primary',
  accent: '--wl-accent',
  heading_font: '--wl-heading-font',
  body_font: '--wl-body-font',
}

function brandStyle(branding) {
  const style = {}
  for (const [key, variable] of Object.entries(TOKEN_ALIASES)) {
    const value = branding?.[key]
    if (typeof value === 'string' && value.trim()) style[variable] = value.trim()
  }
  return style
}

export default function PublicRoom({ path }) {
  const [state, setState] = useState({ data: null, error: null, loading: true })

  useEffect(() => {
    let cancelled = false
    setState({ data: null, error: null, loading: true })
    api
      .resolveLink(path, window.location.host)
      .then((data) => !cancelled && setState({ data, error: null, loading: false }))
      .catch((error) => !cancelled && setState({ data: null, error, loading: false }))
    return () => {
      cancelled = true
    }
  }, [path])

  const branding = state.data?.white_label?.branding
  const style = useMemo(() => brandStyle(branding), [branding])

  if (state.loading) {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <Spinner label="Opening room" />
      </div>
    )
  }

  // A 200 that does not carry a resolvable room is treated the same as a
  // failure. Without this the render below would destructure undefined and
  // crash the page a buyer is trying to open.
  if (state.error || !state.data?.white_label) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        <ErrorNote error={state.error} />
        <p className="mt-4 text-sm text-muted-foreground">
          This link may have been shared before it finished setting up, or the domain may have
          moved. Ask for the link again rather than editing it by hand — the security code at the
          end of the URL is what identifies the room.
        </p>
        {/* ErrorNote already prints the server's message, so this only speaks when
            the response itself was well-formed and carried nothing to show. */}
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
                <Icon name="globe" size={13} />
                {whiteLabel.domain}
              </span>
            </Badge>
          ) : (
            <Badge tone="neutral">
              <span className="inline-flex items-center gap-1.5">
                <Icon name="shield" size={13} />
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
          <Icon name="shield" size={14} className="mt-0.5 shrink-0" />
          <span>
            This address works on the default host and on any domain configured for the room, so a
            link stays usable if the domain later changes.
          </span>
        </p>
      </div>
    </div>
  )
}
