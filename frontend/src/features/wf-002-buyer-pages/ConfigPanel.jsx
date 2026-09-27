import { useEffect, useState } from 'react'
import { Button, Field, inputClass } from '@/components/ui'
import Glyph from './icons'
import { Note, Toggle } from './primitives'
import { keyLabel } from './BlockRenderer'

/**
 * The configuration panel: "Select the fragment on the page, then set its
 * fields in the configuration panel."
 *
 * Fields come from the server's catalogue rather than from a hard-coded list, so
 * a team that ships its own fragment set gets a panel for it with no change to
 * this file. Fields the catalogue does not declare are still editable, as a
 * JSON object, because a fragment whose field list the vendor never published
 * must not become a fragment nobody can configure.
 */

function FieldInput({ field, value, documents, onChange, idPrefix }) {
  const id = `${idPrefix}-${field.key}`
  const label = field.aria ? `${field.label} (accessibility)` : field.label

  if (field.type === 'boolean') {
    return (
      <Toggle
        id={id}
        label={label}
        hint={field.help}
        checked={Boolean(value)}
        onChange={(next) => onChange(field.key, next)}
      />
    )
  }

  if (field.type === 'select') {
    return (
      <Field label={label} id={id} hint={field.help}>
        <select
          id={id}
          className={inputClass}
          value={value ?? ''}
          onChange={(event) => onChange(field.key, event.target.value)}
        >
          <option value="">Not set</option>
          {field.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </Field>
    )
  }

  if (field.type === 'document') {
    // The selectors take a file from the room's documents, so the choice is a
    // pick from this room's documents rather than a free-text id.
    return (
      <Field label={label} id={id} hint={field.help}>
        <select
          id={id}
          className={inputClass}
          value={value ?? ''}
          onChange={(event) => onChange(field.key, event.target.value || null)}
        >
          <option value="">Not set</option>
          {documents.map((doc) => (
            <option key={doc.id} value={doc.id}>
              {doc.data?.title || doc.id}
            </option>
          ))}
        </select>
      </Field>
    )
  }

  if (field.type === 'textarea') {
    return (
      <Field label={label} id={id} hint={field.help}>
        <textarea
          id={id}
          rows={4}
          className={`${inputClass} py-2`}
          value={value ?? ''}
          onChange={(event) => onChange(field.key, event.target.value)}
        />
      </Field>
    )
  }

  return (
    <Field label={label} id={id} hint={field.help}>
      <input
        id={id}
        type={field.type === 'number' ? 'number' : field.type === 'url' ? 'url' : 'text'}
        min={field.min}
        max={field.max}
        step={field.type === 'number' ? 1 : undefined}
        className={inputClass}
        value={value ?? ''}
        onChange={(event) =>
          onChange(
            field.key,
            field.type === 'number'
              ? event.target.value === ''
                ? null
                : Number(event.target.value)
              : event.target.value,
          )
        }
      />
    </Field>
  )
}

function OwnFieldEditor({ block, onSave, saving }) {
  const declared = new Set((block.fields || []).map((field) => field.key))
  const extra = Object.fromEntries(
    Object.entries(block.config || {}).filter(([key]) => !declared.has(key)),
  )
  const [text, setText] = useState(() => JSON.stringify(extra, null, 2))
  const [error, setError] = useState(null)

  useEffect(() => {
    setText(JSON.stringify(extra, null, 2))
    setError(null)
    // Re-seed only when the stored set of own fields changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(Object.keys(extra).sort())])

  function apply() {
    if (!text.trim()) {
      onSave({})
      return
    }
    try {
      const parsed = JSON.parse(text)
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        setError('These fields must be a JSON object, for example {"campaign_id": "q4"}.')
        return
      }
      setError(null)
      onSave(parsed)
    } catch (parseError) {
      setError(`Not valid JSON: ${parseError.message}`)
    }
  }

  return (
    <div className="space-y-2">
      <Field
        label="Additional fields (JSON)"
        id={`${block.id}-own`}
        hint="Any field the catalogue does not declare. Stored as written, no migration needed."
      >
        <textarea
          id={`${block.id}-own`}
          rows={5}
          spellCheck={false}
          className={`${inputClass} py-2 font-mono text-xs`}
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      </Field>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <Button icon="check" onClick={apply} disabled={saving}>
        {saving ? 'Saving…' : 'Save additional fields'}
      </Button>
    </div>
  )
}

export default function ConfigPanel({ block, fragment, documents, onChange, saving, error }) {
  if (!block) {
    return (
      <p className="text-sm text-muted-foreground">
        Select a fragment on the page to configure it, or add one from the palette.
      </p>
    )
  }

  const fields = fragment?.fields || block.fields || []
  const declared = new Set(fields.map((field) => field.key))
  const own = Object.keys(block.config || {}).filter((key) => !declared.has(key))

  return (
    <div className="space-y-4">
      <header className="flex items-start gap-2">
        <span className="mt-0.5 shrink-0 text-accent">
          <Glyph name={fragment?.icon || 'schema'} size={16} />
        </span>
        <div className="min-w-0">
          <h3 className="font-mono text-sm font-semibold text-foreground">
            {fragment?.name || block.name || block.fragment}
          </h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            <span className="font-mono">{block.set || fragment?.set}</span>
            {fragment?.documented_fields === false &&
              ' · the documentation lists no fields for this fragment'}
          </p>
        </div>
      </header>

      {error && <Note tone="warn" title="Rejected">{error}</Note>}

      {fragment?.note && <Note tone="info">{fragment.note}</Note>}

      {fields.length === 0 ? (
        <Note tone="info" title="No documented fields">
          The research behind this build records a field list for some fragments and not for
          others. This is one of the others, so nothing is invented here: use the JSON editor
          below to store whatever fields it needs.
        </Note>
      ) : (
        <div className="space-y-3">
          {fields.map((field) => (
            <FieldInput
              key={field.key}
              field={field}
              value={(block.config || {})[field.key]}
              documents={documents}
              onChange={onChange}
              idPrefix={block.id}
            />
          ))}
        </div>
      )}

      {own.length > 0 && <OwnFieldEditor block={block} onSave={onChange} saving={saving} />}
    </div>
  )
}

export { keyLabel }
