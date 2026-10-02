import { useCallback, useMemo, useState } from 'react'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'
import { libraryApi } from './api'
import { Checkbox, ChoiceGroup, SpacedField, Glyph } from './primitives'
import { ICONS } from './icons'

/**
 * Library search: find content, then put it in a room.
 *
 * Three things this page is careful about, because each is a researched
 * behaviour rather than a UI preference:
 *
 * 1. The query is a real query, not a keyword box. Search fields, return
 *    fields, a filter expression, a sort and a page size are all part of the
 *    contract, and the limits (150-character term, 0-100 page size, filter
 *    depth 2) are read from the server so this file cannot drift from them.
 * 2. When broadening rescues a zero-hit search the server reports
 *    `actualSearchTerm`, and this page says so. Showing results for words the
 *    user did not type without saying so is how a seller ships the wrong deck.
 * 3. Asset URLs expire. The expiry travels with every response and is shown,
 *    so nobody caches a thumbnail that breaks tomorrow.
 *
 * Ported from `frontend/src/pages/LibrarySearch.jsx` on
 * `feature/WF-010-search-the-content-library-to-assemble-a-room`. It called eight
 * methods on the shared `api` object and imported `Checkbox`, `ChoiceGroup` and
 * six icons that only existed because that branch had edited `components/ui.jsx`
 * and `lib/api.js`. All of those are shared files, so here it calls `./api` (which
 * wraps `apiRequest`) and takes the two primitives and the glyph paths from this
 * folder. The page's behaviour is unchanged.
 */

const OPERATOR_LABELS = {
  equal: 'is',
  in: 'is any of',
  greaterThan: 'is more than',
  greaterThanOrEqual: 'is at least',
  lessThan: 'is less than',
  lessThanOrEqual: 'is at most',
}

const FIELD_LABELS = {
  name: 'Title',
  description: 'Description',
  body: 'Full text',
  properties: 'Custom properties',
  format: 'Format',
  publishDate: 'Published',
  pages: 'Pages',
  profile: 'Profile',
  versionId: 'Version id',
  majorVersion: 'Major version',
  minorVersion: 'Minor version',
  downloadUrl: 'Download URL',
  thumbnailUrl: 'Thumbnail URL',
  applicationUrls: 'Application URLs',
  repository: 'Repository',
  type: 'Type',
  teamsiteId: 'Site id',
  pageThumbnailUrls: 'Page thumbnails',
  latestVersion: 'Latest version',
  latestApprovedVersion: 'Latest approved',
}

const RETURN_PRESENTATION = {
  description: (value) => value,
  properties: (value) => JSON.stringify(value),
  applicationUrls: (value) => JSON.stringify(value),
  pageThumbnailUrls: (value) => `${(value || []).length} pages`,
  thumbnailUrl: () => 'thumbnail',
  downloadUrl: () => 'download',
}

let filterSequence = 0
function nextFilterId() {
  filterSequence += 1
  return `f${filterSequence}`
}

function blankCondition() {
  return { key: nextFilterId(), field: '', operator: 'equal', value: '' }
}

function blankGroup(depth) {
  return { key: nextFilterId(), operator: 'and', depth, conditions: [blankCondition()], group: null }
}

function buildGroupBody(group) {
  if (!group) return null
  const conditions = group.conditions
    .filter((condition) => condition.field)
    .map((condition) => ({
      field: condition.field,
      operator: condition.operator,
      value: coerce(condition.value, condition.operator),
    }))
  const nested = buildGroupBody(group.group)
  if (nested) conditions.push(nested)
  if (conditions.length === 0) return null
  if (conditions.length === 1) return conditions[0]
  return { [group.operator]: conditions }
}

/** Turn the text in a filter row into the type the operator needs. */
function coerce(raw, operator) {
  const text = String(raw).trim()
  if (text === '') return ''
  if (operator === 'in') {
    return text
      .split(',')
      .map((part) => part.trim())
      .filter(Boolean)
  }
  if (operator === 'equal' && /^-?\d+(\.\d+)?$/.test(text)) return Number(text)
  return text
}

/* -- the filter editor ----------------------------------------------------- */

function ConditionRow({ condition, fields, onChange, onRemove }) {
  return (
    <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,0.8fr)_minmax(0,1fr)_auto]">
      <div>
        <label className="sr-only" htmlFor={`${condition.key}-field`}>
          Filter field
        </label>
        <select
          id={`${condition.key}-field`}
          className={inputClass}
          value={condition.field}
          onChange={(event) => onChange({ ...condition, field: event.target.value })}
        >
          <option value="">Choose a field…</option>
          {fields.map((field) => (
            <option key={field.path} value={field.path}>
              {FIELD_LABELS[field.path] || field.path}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="sr-only" htmlFor={`${condition.key}-operator`}>
          Filter operator
        </label>
        <select
          id={`${condition.key}-operator`}
          className={inputClass}
          value={condition.operator}
          onChange={(event) => onChange({ ...condition, operator: event.target.value })}
        >
          {Object.entries(OPERATOR_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="sr-only" htmlFor={`${condition.key}-value`}>
          Filter value
        </label>
        <input
          id={`${condition.key}-value`}
          className={inputClass}
          placeholder={condition.operator === 'in' ? 'pdf, pptx' : 'value'}
          value={condition.value}
          onChange={(event) => onChange({ ...condition, value: event.target.value })}
        />
      </div>
      <Button icon="close" onClick={onRemove} className="justify-self-start sm:justify-self-auto">
        <span className="sr-only sm:not-sr-only">Remove</span>
      </Button>
    </div>
  )
}

/** One boolean group. Recursive, but the depth comes from the server contract,
 *  so the editor cannot offer nesting the API will reject. */
function FilterGroupEditor({ group, fields, maxDepth, onChange, onRemove, root = false }) {
  const canNest = group.depth < maxDepth
  return (
    <fieldset className={root ? '' : 'mt-2 border-l-2 border-border-subtle/40 pl-3'}>
      <legend className="sr-only">{root ? 'Filter' : 'Nested filter group'}</legend>
      <div className="flex flex-wrap items-center gap-2">
        <ChoiceGroup
          legend={root ? 'Match' : 'Nested match'}
          name={`group-${group.key}`}
          value={group.operator}
          onChange={(operator) => onChange({ ...group, operator })}
          options={[
            { value: 'and', label: 'all of' },
            { value: 'or', label: 'any of' },
          ]}
        />
        {!root && (
          <Button icon="trash" onClick={onRemove}>
            Remove group
          </Button>
        )}
      </div>

      <div className="mt-2 space-y-2">
        {group.conditions.map((condition) => (
          <ConditionRow
            key={condition.key}
            condition={condition}
            fields={fields}
            onChange={(next) =>
              onChange({
                ...group,
                conditions: group.conditions.map((item) =>
                  item.key === condition.key ? next : item,
                ),
              })
            }
            onRemove={() =>
              onChange({ ...group, conditions: group.conditions.filter((item) => item.key !== condition.key) })
            }
          />
        ))}
      </div>

      <div className="mt-2 flex flex-wrap gap-2">
        <Button icon="plus" onClick={() => onChange({ ...group, conditions: [...group.conditions, blankCondition()] })}>
          Condition
        </Button>
        {canNest ? (
          <Button
            icon="plus"
            onClick={() => onChange({ ...group, group: group.group || blankGroup(group.depth + 1) })}
          >
            Nested group
          </Button>
        ) : (
          <span className="self-center text-xs text-muted-foreground">
            Maximum filter depth reached ({maxDepth}).
          </span>
        )}
      </div>

      {group.group && (
        <FilterGroupEditor
          group={group.group}
          fields={fields}
          maxDepth={maxDepth}
          onChange={(next) => onChange({ ...group, group: next })}
          onRemove={() => onChange({ ...group, group: null })}
        />
      )}
    </fieldset>
  )
}

/* -- one result ------------------------------------------------------------ */

function ResultRow({ document, selected, onToggle }) {
  const returnField = (name) => document[name]
  return (
    <li className="border-b border-border-subtle/15 last:border-b-0">
      <div className="flex items-start gap-3 py-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggle(document.id)}
          aria-label={`Select ${document.name || 'this document'}`}
          className="mt-1 h-4 w-4 shrink-0 accent-[var(--color-accent)]"
        />
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-sm font-semibold text-foreground">
            {document.name || 'Untitled document'}
          </p>
          {document.description && (
            <p className="mt-0.5 line-clamp-2 text-sm text-muted-foreground">{document.description}</p>
          )}
          <div className="mt-2 flex flex-wrap gap-1.5">
            {document.format && <Badge>{document.format}</Badge>}
            {document.publishDate && <Badge tone="update">{document.publishDate}</Badge>}
            {document.pages !== null && document.pages !== undefined && (
              <Badge>{document.pages} pages</Badge>
            )}
            {Array.isArray(document._matchedFields) && document._matchedFields.map((field) => (
              <Badge key={field} tone="insert">
                matched {FIELD_LABELS[field] || field}
              </Badge>
            ))}
          </div>
          {document.downloadUrl && (
            <p className="mt-2 font-mono text-xs text-accent">Download available</p>
          )}
        </div>
        <div className="hidden shrink-0 text-right font-mono text-xs text-muted-foreground lg:block">
          {Object.keys(document)
            .filter((key) => !['_matchedFields', 'name', 'id'].includes(key) && returnField(key) !== null)
            .slice(0, 4)
            .map((key) => (
              <p key={key} className="max-w-[14rem] truncate">
                <span className="text-foreground">{FIELD_LABELS[key] || key}: </span>
                {String(RETURN_PRESENTATION[key] ? RETURN_PRESENTATION[key](returnField(key)) : returnField(key))}
              </p>
            ))}
        </div>
      </div>
    </li>
  )
}

/* -- the page -------------------------------------------------------------- */

export default function LibrarySearch() {
  const contract = useAsync(() => libraryApi.contract(), [])
  const fields = useAsync(() => libraryApi.fields(), [])
  const rooms = useAsync(() => libraryApi.rooms(), [])
  const saved = useAsync(() => libraryApi.savedSearches(), [])

  const [term, setTerm] = useState('')
  const [searchFields, setSearchFields] = useState(null)
  const [returnFields, setReturnFields] = useState(null)
  const [pageSize, setPageSize] = useState(null)
  const [broaden, setBroaden] = useState(false)
  const [sortField, setSortField] = useState('')
  const [sortDirection, setSortDirection] = useState('desc')
  const [group, setGroup] = useState(() => blankGroup(1))

  const [result, setResult] = useState(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState(null)
  const [tokenExpired, setTokenExpired] = useState(false)
  const [selected, setSelected] = useState([])
  const [chosenTargetRoom, setChosenTargetRoom] = useState('')
  const [assembling, setAssembling] = useState(false)
  const [assembly, setAssembly] = useState(null)
  const [saveName, setSaveName] = useState('')
  const [activeSearchId, setActiveSearchId] = useState(null)

  const limits = contract.data?.limits
  const availableFields = useMemo(() => {
    const discovered = (fields.data?.fields || []).map((entry) => entry.path)
    const known = Object.keys(FIELD_LABELS)
    return [...new Set([...discovered, ...known])].sort().map((path) => ({ path }))
  }, [fields.data])

  // The three controls below are seeded from the server contract, so the
  // defaults here are the server's defaults rather than a second copy of them.
  // They read as `null` until the operator or a saved search sets them, so the
  // contract's value is the fallback. Deriving the fallback during render
  // replaces an effect that wrote three states on arrival -- and, more to the
  // point, it stops the first render after the contract loads from offering the
  // operator the page's hardcoded fallbacks.
  const seededSearchFields = searchFields ?? contract.data?.searchFields
  const seededReturnFields = returnFields ?? contract.data?.returnFields?.default
  const seededPageSize = pageSize ?? contract.data?.limits?.defaultPageSize

  const effectivePageSize = seededPageSize ?? 20

  // Which room results are assembled into. An explicit choice wins, otherwise
  // the first room, derived rather than copied into state by an effect.
  const targetRoom = chosenTargetRoom || rooms.data?.records?.[0]?.id || ''

  const buildBody = useCallback(
    (overrides = {}) => {
      const body = {}
      if (term.trim()) body.term = term.trim()
      const options = {}
      // An explicitly empty list is sent as one, not dropped: the server treats
      // a missing list as "use the defaults", so silently omitting it would make
      // unchecking every field look like it had done nothing.
      if (seededSearchFields) options.searchFields = seededSearchFields
      if (seededReturnFields) options.returnFields = seededReturnFields
      options.pageSize = effectivePageSize
      if (broaden) options.enableSuggestedQueryResults = true
      if (Object.keys(options).length) body.options = options
      const filter = buildGroupBody(group)
      if (filter) body.filter = filter
      if (sortField) body.sort = [{ field: sortField, direction: sortDirection }]
      return { ...body, ...overrides }
    },
    [term, seededSearchFields, seededReturnFields, effectivePageSize, broaden, group, sortField, sortDirection],
  )

  async function run(continuationToken) {
    setRunning(true)
    setError(null)
    if (!continuationToken) {
      setTokenExpired(false)
      setResult(null)
      setAssembly(null)
    }
    try {
      const body = await libraryApi.search(buildBody(), continuationToken)
      setResult((previous) =>
        continuationToken && previous
          ? { ...body, documents: [...previous.documents, ...body.documents] }
          : body,
      )
    } catch (caught) {
      setError(caught)
      if (String(caught.message || caught).includes('continuationToken')) setTokenExpired(true)
    } finally {
      setRunning(false)
    }
  }

  function toggleField(list, setList, value) {
    setList(list.includes(value) ? list.filter((item) => item !== value) : [...list, value])
  }

  async function assemble() {
    if (!targetRoom || selected.length === 0) return
    setAssembling(true)
    setError(null)
    try {
      const items = selected
        .map((id) => result?.documents.find((document) => document.id === id))
        .filter(Boolean)
        .map((document) => {
          const { _matchedFields, ...rest } = document
          return rest
        })
      const response = await libraryApi.assemble({
        room_id: targetRoom,
        items,
        search_id: activeSearchId || undefined,
      })
      setAssembly(response)
      setSelected([])
    } catch (caught) {
      setError(caught)
    } finally {
      setAssembling(false)
    }
  }

  async function saveCurrentSearch() {
    if (!saveName.trim()) return
    try {
      await libraryApi.saveSearch({ name: saveName.trim(), query: buildBody() })
      setSaveName('')
      saved.refetch()
    } catch (caught) {
      setError(caught)
    }
  }

  function loadSaved(record) {
    const query = record.data?.query || {}
    setTerm(query.term || '')
    if (query.options?.searchFields) setSearchFields(query.options.searchFields)
    if (query.options?.returnFields) setReturnFields(query.options.returnFields)
    if (query.options?.pageSize) setPageSize(query.options.pageSize)
    setBroaden(Boolean(query.options?.enableSuggestedQueryResults))
    setSortField(query.sort?.[0]?.field || '')
    setSortDirection(query.sort?.[0]?.direction || 'desc')
    setGroup(query.filter ? groupFromBody(query.filter, 1) : blankGroup(1))
    // Remember which saved search is loaded, so assembling from it records the
    // provenance on the room content and in the audit row.
    setActiveSearchId(record.id)
  }

  function resetAll() {
    setTerm('')
    setGroup(blankGroup(1))
    setSortField('')
    setBroaden(false)
    setResult(null)
    setSelected([])
    setAssembly(null)
    setError(null)
    setTokenExpired(false)
    setActiveSearchId(null)
  }

  if (contract.loading || fields.loading) return <Spinner label="Loading the search contract" />
  if (contract.error) return <ErrorNote error={contract.error} onRetry={contract.refetch} />

  const documents = result?.documents || []
  const selectedCount = selected.length

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-mono text-2xl font-semibold">Library search</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Search the content library, then put the documents you picked into a room. Every
            field name resolves to whatever JSON path your library actually uses, so a field
            added yesterday is searchable today.
          </p>
        </div>
        <Button icon="refresh" onClick={resetAll}>
          Reset
        </Button>
      </header>

      {/* -- query ------------------------------------------------------- */}
      <Card>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            run()
          }}
        >
          <Field
            label="Search term"
            id="library-term"
            hint={
              limits
                ? `Up to ${limits.maxTermLength} characters. Leave it empty to list the whole library.`
                : undefined
            }
          >
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="relative flex-1">
                <span className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted-foreground">
                  <Icon name="search" />
                </span>
                <input
                  id="library-term"
                  className={`${inputClass} pl-10`}
                  value={term}
                  maxLength={limits?.maxTermLength}
                  onChange={(event) => setTerm(event.target.value)}
                />
              </div>
              <Button type="submit" variant="primary" icon="search" disabled={running}>
                {running ? 'Searching…' : 'Search'}
              </Button>
            </div>
          </Field>

          <div className="grid gap-4 lg:grid-cols-2">
            <fieldset>
              <legend className="mb-1.5 text-xs font-medium text-muted-foreground">
                Search these fields
              </legend>
              <div className="grid gap-1 sm:grid-cols-2">
                {(contract.data.searchFields || []).map((field) => (
                  <Checkbox
                    key={field}
                    label={FIELD_LABELS[field] || field}
                    checked={(seededSearchFields || []).includes(field)}
                    onChange={() => toggleField(seededSearchFields || [], setSearchFields, field)}
                  />
                ))}
              </div>
              {seededSearchFields && seededSearchFields.length === 0 && (
                <p className="mt-1 text-xs text-amber-300">
                  No search fields selected, so a term cannot match anything.
                </p>
              )}
            </fieldset>

            <fieldset>
              <legend className="mb-1.5 text-xs font-medium text-muted-foreground">
                Return these fields
              </legend>
              <div className="grid gap-1 sm:grid-cols-2">
                {[
                  ...contract.data.returnFields.default,
                  ...contract.data.returnFields.optIn,
                ].map((field) => (
                  <Checkbox
                    key={field}
                    label={FIELD_LABELS[field] || field}
                    checked={(seededReturnFields || []).includes(field)}
                    onChange={() => toggleField(seededReturnFields || [], setReturnFields, field)}
                  />
                ))}
              </div>
            </fieldset>
          </div>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Results per page" id="library-page-size">
              <select
                id="library-page-size"
                className={inputClass}
                value={pageSize}
                onChange={(event) => setPageSize(Number(event.target.value))}
              >
                {[5, 10, 20, 40, 50, 100]
                  .filter((size) => limits && size >= limits.minPageSize && size <= limits.maxPageSize)
                  .map((size) => (
                    <option key={size} value={size}>
                      {size}
                    </option>
                  ))}
              </select>
            </Field>

            <Field label="Sort by" id="library-sort-field">
              <select
                id="library-sort-field"
                className={inputClass}
                value={sortField}
                onChange={(event) => setSortField(event.target.value)}
              >
                <option value="">Relevance</option>
                {availableFields.map((field) => (
                  <option key={field.path} value={field.path}>
                    {FIELD_LABELS[field.path] || field.path}
                  </option>
                ))}
              </select>
            </Field>

            <ChoiceGroup
              legend="Sort direction"
              name="library-sort-direction"
              className="self-end"
              value={sortDirection}
              onChange={setSortDirection}
              options={[
                { value: 'desc', label: 'Descending' },
                { value: 'asc', label: 'Ascending' },
              ]}
            />

            <div className="self-end">
              <Checkbox
                label="Broaden a zero-hit search"
                hint="Retries with related terms and tells you which one matched."
                checked={broaden}
                onChange={setBroaden}
              />
            </div>
          </div>

          <details className="rounded-lg border border-border-subtle/25 bg-background/30 p-3">
            <summary className="min-h-11 cursor-pointer list-none text-sm font-medium text-foreground">
              <span className="inline-flex items-center gap-2">
                <Glyph name={ICONS.filter} />
                Filter
                {buildGroupBody(group) ? ' (1 group)' : ''}
              </span>
            </summary>
            <div className="mt-3">
              <p className="mb-3 text-xs text-muted-foreground">
                Groups nest up to {limits?.maxFilterDepth} levels deep. Custom properties are
                filterable by name, for example{' '}
                <span className="font-mono text-foreground">custom.Region</span>.
              </p>
              <FilterGroupEditor
                group={group}
                fields={availableFields}
                maxDepth={limits?.maxFilterDepth || 2}
                onChange={setGroup}
                onRemove={() => setGroup(blankGroup(1))}
              />
            </div>
          </details>
        </form>
      </Card>

      {error && <ErrorNote error={error} onRetry={() => run()} />}

      {tokenExpired && (
        <div
          role="status"
          className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-foreground"
        >
          Paging cursors expire, so this page was cut off. Run the search again to pick up where
          the results are now.
        </div>
      )}

      {/* -- results ----------------------------------------------------- */}
      {running && !result && <Spinner label="Searching the library" />}

      {result && (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="font-mono text-base font-semibold">
                {result.totalCount} {result.totalCount === 1 ? 'document' : 'documents'}
              </h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                showing {documents.length} · scanned {result.scanned} in {result.queryTimeInMs} ms
                · repository {result.repository}
              </p>
            </div>
            {documents.length > 0 && (
              <Button
                onClick={() =>
                  setSelected((current) => {
                    const ids = documents.map((document) => document.id)
                    const allSelected = ids.every((id) => current.includes(id))
                    return allSelected
                      ? current.filter((id) => !ids.includes(id))
                      : [...new Set([...current, ...ids])]
                  })
                }
              >
                {documents.every((document) => selected.includes(document.id))
                  ? 'Clear page'
                  : 'Select page'}
              </Button>
            )}
          </div>

          {result.actualSearchTerm && (
            <p className="mt-3 rounded-lg border border-accent/30 bg-accent/10 p-3 text-sm text-foreground">
              Nothing matched “{term.trim()}”. These results are for{' '}
              <span className="font-mono text-accent">{result.actualSearchTerm}</span>, a related
              term.
            </p>
          )}

          {result.assetUrlTtlDays && (
            <p className="mt-3 text-xs text-muted-foreground">
              <Glyph name={ICONS.clock} size={14} className="mr-1 inline" />
              Asset links in this result expire {new Date(result.assetUrlsExpireAt).toLocaleString()}.
              Do not cache them.
            </p>
          )}

          {documents.length === 0 ? (
            <div className="mt-4">
              <EmptyState
                title="No documents matched"
                description={
                  broaden
                    ? 'Broadening is on and still found nothing. Try a shorter term or drop a filter.'
                    : 'Try a shorter term, widen the search fields, or turn on broadening.'
                }
              />
            </div>
          ) : (
            <>
              <ul className="mt-3">
                {documents.map((document) => (
                  <ResultRow
                    key={document.id}
                    document={document}
                    selected={selected.includes(document.id)}
                    onToggle={(id) =>
                      setSelected((current) =>
                        current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
                      )
                    }
                  />
                ))}
              </ul>

              {result.continuationToken && (
                <div className="mt-4">
                  <Button
                    icon="chevron"
                    onClick={() => run(result.continuationToken)}
                    disabled={running}
                  >
                    {running ? 'Loading…' : `Load ${result.totalCount - documents.length} more`}
                  </Button>
                </div>
              )}
            </>
          )}
        </Card>
      )}

      {/* -- the tray ---------------------------------------------------- */}
      {selectedCount > 0 && (
        <Card className="border-accent/40">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="flex items-center gap-2 font-mono text-base font-semibold">
                <Glyph name={ICONS.tray} />
                {selectedCount} selected
              </h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Adding is one audited write, so the room and the audit log move together.
              </p>
            </div>
            <div className="flex flex-wrap items-end gap-2">
              <Field label="Add to room" id="library-target-room">
                <select
                  id="library-target-room"
                  className={inputClass}
                  value={targetRoom}
                  onChange={(event) => setChosenTargetRoom(event.target.value)}
                >
                  {(rooms.data?.records || []).map((room) => (
                    <option key={room.id} value={room.id}>
                      {room.data?.name || room.id}
                    </option>
                  ))}
                </select>
              </Field>
              <Button
                variant="primary"
                icon="plus"
                onClick={assemble}
                disabled={assembling || !targetRoom}
              >
                {assembling ? 'Adding…' : `Add ${selectedCount} to room`}
              </Button>
              <Button onClick={() => setSelected([])}>Clear</Button>
            </div>
          </div>

          {assembly && (
            <p className="mt-3 rounded-lg border border-accent/30 bg-accent/10 p-3 text-sm text-foreground">
              Added {assembly.added_count} to the room
              {assembly.skipped_count > 0 && `, skipped ${assembly.skipped_count} already there`}.
              Find it in the audit log.
            </p>
          )}
        </Card>
      )}

      {/* -- saved searches ---------------------------------------------- */}
      <Card>
        <h2 className="flex items-center gap-2 font-mono text-base font-semibold">
          <Glyph name={ICONS.bookmark} />
          Saved searches
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Saving is one audited write. Searching is a read and leaves nothing in the trail, so the
          named query is where a team&rsquo;s searching becomes reportable.
        </p>

        <div className="mt-3 flex flex-wrap items-end gap-2">
          <SpacedField label="Name" id="library-save-name" className="min-w-[14rem] flex-1">
            <input
              id="library-save-name"
              className={inputClass}
              value={saveName}
              onChange={(event) => setSaveName(event.target.value)}
            />
          </SpacedField>
          <Button onClick={saveCurrentSearch} disabled={!saveName.trim()}>
            Save this search
          </Button>
        </div>

        {(saved.data?.records || []).length === 0 ? (
          <p className="mt-4 text-sm text-muted-foreground">No saved searches yet.</p>
        ) : (
          <ul className="mt-4 divide-y divide-border-subtle/15">
            {saved.data.records.map((record) => (
              <li key={record.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <div className="min-w-0">
                  <p className="truncate font-mono text-sm text-foreground">{record.data?.name}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {record.data?.query?.term || 'all content'}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button onClick={() => loadSaved(record)}>Load</Button>
                  <Button
                    icon="trash"
                    onClick={async () => {
                      await libraryApi.deleteSavedSearch(record.id)
                      saved.refetch()
                    }}
                  >
                    Delete
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

/** Rebuild an editable group tree from a stored filter body. */
function groupFromBody(node, depth) {
  if (!node || typeof node !== 'object') return blankGroup(depth)
  const operator = node.and ? 'and' : 'or'
  const children = node.and || node.or || []
  const conditions = []
  let nested = null

  for (const child of children) {
    if (child.field) {
      conditions.push({
        key: nextFilterId(),
        field: child.field,
        operator: child.operator || 'equal',
        value: Array.isArray(child.value) ? child.value.join(', ') : String(child.value ?? ''),
      })
    } else {
      nested = groupFromBody(child, depth + 1)
    }
  }

  return {
    key: nextFilterId(),
    operator,
    depth,
    conditions: conditions.length ? conditions : [blankCondition()],
    group: nested,
  }
}
