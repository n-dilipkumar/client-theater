import { useState } from 'react'
import { apiRequest } from '@/lib/api'
import { Button, Field, inputClass } from './primitives'

/**
 * The two switches from the Share pop-up, in one place.
 *
 * `Link expiry settings` and `Other settings -> Restrict Number of Views` are two
 * separate switches in the vendor's UI, and this build keeps them separate rather
 * than folding both constraints into one form: the research is explicit that they
 * are "orthogonal, independently toggleable", and one form with one Save button
 * would make it look like changing one changes the other.
 *
 * Each therefore saves on its own, so a seller who only wants to raise a view cap
 * does not also restart an expiry clock by touching the expiry field.
 */
export default function WindowEditor({ window, actions, onChange }) {
  const [expiryDays, setExpiryDays] = useState(
    window.expiry.days === null ? '30' : String(window.expiry.days),
  )
  const [maxViews, setMaxViews] = useState(
    window.view_limit.max_views === null ? '25' : String(window.view_limit.max_views),
  )
  const [busy, setBusy] = useState('')
  const [error, setError] = useState(null)

  const { page_id: pageId } = window

  async function call(key, path, options) {
    setBusy(key)
    setError(null)
    try {
      await apiRequest(path, options)
      await onChange()
    } catch (err) {
      setError(err)
    } finally {
      setBusy('')
    }
  }

  function json(method, body) {
    return {
      method,
      headers: { 'Content-Type': 'application/json', 'X-Role': 'room_collaborator' },
      body: JSON.stringify(body),
    }
  }

  const saving = busy !== ''
  const mayEdit = actions?.set_expiry

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {/* -- Link expiry settings -- */}
      <section className="rounded-sm border border-border-subtle bg-surface p-4">
        <h4 className="text-base font-semibold text-foreground">Link expiry settings</h4>
        <p className="mt-1 text-xs text-muted-foreground">
          {window.expiry.enabled
            ? window.expiry.expires_at
              ? `Link stops working ${window.expiry.expires_at.slice(0, 10)} (23:59:59 UTC).`
              : 'Waiting for this page to be published. The count has not started.'
            : 'No expiry. This link does not stop working on a date.'}
        </p>

        <div className="mt-3 flex flex-col gap-3">
          <Field
            id={`expiry-days-${pageId}`}
            label="Enable link expiry for this many days"
            hint="The count starts when the page goes live. A draft page keeps the setting."
          >
            <div className="flex items-center gap-2">
              <input
                id={`expiry-days-${pageId}`}
                type="number"
                min="1"
                max="3650"
                className={inputClass}
                value={expiryDays}
                disabled={!mayEdit}
                onChange={(event) => setExpiryDays(event.target.value)}
              />
              <span className="shrink-0 text-sm text-muted-foreground">days</span>
            </div>
          </Field>

          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              disabled={!mayEdit || saving}
              onClick={() =>
                call(
                  'expiry',
                  `/wf-014/pages/${pageId}/expiry`,
                  json('PUT', { enabled: true, days: Number(expiryDays) }),
                )
              }
            >
              {busy === 'expiry' ? 'Saving' : window.expiry.enabled ? 'Change expiry' : 'Enable expiry'}
            </Button>
            {window.expiry.enabled && (
              <Button
                variant="ghost"
                disabled={!mayEdit || saving}
                onClick={() =>
                  call(
                    'clear-expiry',
                    `/wf-014/pages/${pageId}/expiry`,
                    json('DELETE', {}),
                  )
                }
              >
                Remove expiry
              </Button>
            )}
          </div>
        </div>
      </section>

      {/* -- Other settings: restrict number of views -- */}
      <section className="rounded-sm border border-border-subtle bg-surface p-4">
        <h4 className="text-base font-semibold text-foreground">Restrict number of views</h4>
        <p className="mt-1 text-xs text-muted-foreground">
          {window.view_limit.enabled
            ? `${window.view_limit.views} of ${window.view_limit.max_views} views used.`
            : 'No limit. This link can be opened any number of times.'}
        </p>

        {window.view_limit.enabled && (
          <div className="mt-3" role="img" aria-label={`${window.view_limit.views} of ${window.view_limit.max_views} views used`}>
            {/* `rounded-xs` rather than `rounded-full`: the design system reserves
                full rounding for circles, and near-square shapes are the signal
                that this is enterprise software. */}
            <div className="h-1.5 w-full rounded-xs bg-muted">
              <div
                className={`h-1.5 rounded-xs ${window.view_limit.capped ? 'bg-destructive' : 'bg-accent'}`}
                style={{
                  width: `${Math.min(
                    100,
                    (window.view_limit.views / Math.max(window.view_limit.max_views, 1)) * 100,
                  )}%`,
                }}
              />
            </div>
          </div>
        )}

        <div className="mt-3 flex flex-col gap-3">
          <Field
            id={`max-views-${pageId}`}
            label="Close the link after this many views"
            hint="Counted with count_where, so the number is an aggregate over view records."
          >
            <div className="flex items-center gap-2">
              <input
                id={`max-views-${pageId}`}
                type="number"
                min="1"
                className={inputClass}
                value={maxViews}
                disabled={!mayEdit}
                onChange={(event) => setMaxViews(event.target.value)}
              />
              <span className="shrink-0 text-sm text-muted-foreground">views</span>
            </div>
          </Field>

          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              disabled={!mayEdit || saving}
              onClick={() =>
                call(
                  'limit',
                  `/wf-014/pages/${pageId}/view-limit`,
                  json('PUT', { enabled: true, max_views: Number(maxViews) }),
                )
              }
            >
              {busy === 'limit'
                ? 'Saving'
                : window.view_limit.enabled
                  ? 'Change view limit'
                  : 'Restrict views'}
            </Button>
            {window.view_limit.enabled && (
              <Button
                variant="ghost"
                disabled={!mayEdit || saving}
                onClick={() =>
                  call(
                    'clear-limit',
                    `/wf-014/pages/${pageId}/view-limit`,
                    json('DELETE', {}),
                  )
                }
              >
                Remove view limit
              </Button>
            )}
          </div>
        </div>
      </section>

      {error && (
        <p role="alert" className="text-sm text-destructive sm:col-span-2">
          {String(error.message || error)}
        </p>
      )}
    </div>
  )
}
