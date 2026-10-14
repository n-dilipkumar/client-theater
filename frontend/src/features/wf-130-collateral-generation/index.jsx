/**
 * WF-130: generated, personalised collateral from deal data.
 *
 * A seller describes the deal, and the platform assembles a personalised
 * document or deck from a template - demos, docs and a mutual action plan,
 * populated from the deal - which the seller reviews and edits and then
 * publishes to the room. A repeat deal starts from a template, not a blank page.
 *
 * The page reads its kinds, stages, tokens and templates from the server's
 * `/vocabulary` route rather than repeating them, so the panel cannot tell a
 * seller a value the rules do not accept. Every control is a shared primitive
 * from `@/components/ui`, and the local primitives live in `./primitives`.
 *
 * Three things this page deliberately does not do, each from the research:
 *
 *   - It does not call an LLM. The data flow says "LLM or rules assembly", and
 *     the rules engine is the implementation, so the page names that rather than
 *     hiding it.
 *   - It does not render a PDF or a PPTX. The store holds JSON content blocks, so
 *     the artefact is published as a room page rather than as a binary.
 *   - It does not read a CRM. The deal arrives in this form; nothing reaches a
 *     remote system.
 */

import { useState } from 'react'

import {
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'

import {
  STAGE_LABELS,
  collateralApi,
  kindLabel,
  listRooms,
  shortTimestamp,
} from './api'
import { InlineNote, KindBadge, NotBuiltList, SectionList, StateBadge } from './primitives'

/** Everything the page reads, loaded together so the panels cannot disagree. */
function useBoard(roomId, selectedId) {
  const vocabulary = useAsync(() => collateralApi.vocabulary(), [])
  const decisions = useAsync(() => collateralApi.decisions(), [])
  const summary = useAsync(() => collateralApi.summary(roomId), [roomId])
  const listing = useAsync(() => collateralApi.list(roomId), [roomId])
  const assets = useAsync(() => collateralApi.assets(), [])
  const detail = useAsync(
    () => (selectedId ? collateralApi.read(selectedId) : Promise.resolve(null)),
    [selectedId],
  )
  const loading = vocabulary.loading || summary.loading || listing.loading || assets.loading
  const error = vocabulary.error || summary.error || listing.error || assets.error
  return { vocabulary, decisions, summary, listing, assets, detail, loading, error }
}

export function CollateralGenerationPage() {
  const [roomId, setRoomId] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [note, setNote] = useState(null)
  const [problem, setProblem] = useState(null)

  const rooms = useAsync(() => listRooms(), [])
  const board = useBoard(roomId, selectedId)

  function refetchAll() {
    board.summary.refetch()
    board.listing.refetch()
    board.assets.refetch()
    board.detail.refetch()
  }

  async function act(fn, success) {
    setProblem(null)
    setNote(null)
    try {
      const result = await fn()
      setNote(typeof success === 'function' ? success(result) : success)
      refetchAll()
      return result
    } catch (error) {
      setProblem(error)
      return null
    }
  }

  if (board.loading && !board.summary.data) return <Spinner label="Loading collateral" />
  if (board.error) return <ErrorNote error={board.error} onRetry={refetchAll} />

  const vocabulary = board.vocabulary.data
  const summary = board.summary.data
  const rows = board.listing.data?.collateral || []
  const assets = board.assets.data?.assets || []
  const notBuilt = board.decisions.data?.not_implemented || []

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold text-foreground">Collateral generation</h1>
        <p className="text-sm text-muted-foreground">
          Describe the deal and the room assembles a personalised document or deck from a template,
          with sections that stay hidden until the deal is ready. Review, edit, then publish it to
          the room.
        </p>
      </header>

      {note && <InlineNote tone="success">{note}</InlineNote>}
      {problem && <ErrorNote error={problem} />}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Collateral"
          value={summary?.collaterals ?? 0}
          icon="audit"
          hint={`${summary?.by_kind?.document ?? 0} document, ${summary?.by_kind?.deck ?? 0} deck`}
        />
        <StatCard
          label="Published"
          value={summary?.published ?? 0}
          icon="rooms"
          hint={`${summary?.draft ?? 0} draft, ${summary?.withdrawn ?? 0} withdrawn`}
        />
        <StatCard
          label="Hidden sections"
          value={summary?.hidden_sections ?? 0}
          icon="search"
          hint={`${summary?.visible_sections ?? 0} visible, revealed by stage`}
        />
        <StatCard
          label="Source assets"
          value={summary?.assets ?? 0}
          icon="database"
          hint={`${summary?.synced_sections ?? 0} synced section(s)`}
        />
      </div>

      <Card className="flex flex-col gap-3">
        <Field label="Room" id="wf130-room" hint="A collateral belongs to a room.">
          <select
            id="wf130-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => {
              setRoomId(event.target.value)
              setSelectedId(null)
            }}
          >
            <option value="">Every room</option>
            {(rooms.data?.records || []).map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>
      </Card>

      <GenerateCard
        vocabulary={vocabulary}
        roomId={roomId}
        onCreate={(payload) =>
          act(
            () => collateralApi.create(roomId, payload),
            (created) =>
              `Generated ${created.section_count} section(s); ${created.hidden_count} hidden ` +
              `until the deal is ready.`,
          )
        }
      />

      <Card className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Generated collateral</h2>
        {rows.length === 0 ? (
          <EmptyState
            title="No collateral yet"
            description="Pick a room and describe the deal. The assembled document or deck appears here."
          />
        ) : (
          <ul className="flex flex-col">
            {rows.map((item) => (
              <li
                key={item.id}
                className="flex flex-wrap items-center justify-between gap-3 border-b border-border-subtle py-3 last:border-b-0"
              >
                <span className="flex min-w-0 flex-col">
                  <span className="truncate text-sm text-foreground">{item.title}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {item.id} | {kindLabel(item.kind)} | v{item.version} | {item.section_count}{' '}
                    section(s), {item.visible_count} visible, {item.hidden_count} hidden |{' '}
                    {item.deal?.account || 'no account'} at {STAGE_LABELS[item.stage] || item.stage}
                  </span>
                </span>
                <span className="flex flex-wrap items-center gap-2">
                  <StateBadge state={item.state} />
                  <Button icon="audit" onClick={() => setSelectedId(item.id)}>
                    Review
                  </Button>
                  <Button
                    icon="plus"
                    disabled={item.state === 'withdrawn'}
                    onClick={() =>
                      act(() => collateralApi.publish(item.id), `Published ${item.title}.`)
                    }
                  >
                    Publish
                  </Button>
                  <Button
                    variant="danger"
                    icon="trash"
                    disabled={item.state === 'withdrawn'}
                    onClick={() =>
                      act(() => collateralApi.withdraw(item.id), `Withdrew ${item.title}.`)
                    }
                  >
                    Withdraw
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {selectedId && board.detail.data && (
        <ReviewPanel
          key={selectedId}
          collateral={board.detail.data}
          assets={assets}
          onSave={(payload) =>
            act(() => collateralApi.update(selectedId, payload), `Saved v${board.detail.data.version + 1}.`)
          }
          onPublish={() =>
            act(() => collateralApi.publish(selectedId), 'Published to the room.')
          }
          onWithdraw={() => act(() => collateralApi.withdraw(selectedId), 'Withdrawn.')}
          onRefresh={(assetId) =>
            act(
              () => collateralApi.refreshAsset(assetId),
              (result) => `Refreshed ${result.refreshed} live artefact(s).`,
            )
          }
          onClose={() => setSelectedId(null)}
        />
      )}

      <AssetsCard
        assets={assets}
        onCreate={(payload) =>
          act(() => collateralApi.createAsset(payload), `Registered ${payload.key}.`)
        }
        onRefresh={(asset) =>
          act(
            () => collateralApi.refreshAsset(asset.id),
            (result) => `Pushed ${asset.key} across ${result.refreshed} live artefact(s).`,
          )
        }
      />

      <Card className="flex flex-col gap-2">
        <h2 className="text-base font-semibold text-foreground">Not built here</h2>
        <p className="text-xs text-muted-foreground">
          The research names these, and they belong to another workflow or to a deployment. They are
          listed so their absence reads as a decision rather than an oversight.
        </p>
        <NotBuiltList items={notBuilt} />
      </Card>
    </div>
  )
}

function GenerateCard({ vocabulary, roomId, onCreate }) {
  const [account, setAccount] = useState('')
  const [contact, setContact] = useState('')
  const [product, setProduct] = useState('')
  const [stage, setStage] = useState('discovery')
  const [seller, setSeller] = useState('')
  const [templateKey, setTemplateKey] = useState(vocabulary?.default_template || 'proposal_document')
  const [busy, setBusy] = useState(false)

  const templates = vocabulary?.templates || []
  const stages = vocabulary?.deal_stages || Object.keys(STAGE_LABELS)
  const refused = []
  if (!account.trim()) refused.push('An account is required.')

  async function submit() {
    setBusy(true)
    await onCreate({
      deal: {
        account: account.trim(),
        contact: contact.trim(),
        product: product.trim(),
        stage,
        seller: seller.trim(),
      },
      template_key: templateKey,
    })
    setBusy(false)
  }

  return (
    <Card className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold text-foreground">Describe the deal</h2>
      <p className="text-xs text-muted-foreground">
        The deal is assembled into content blocks by the rules engine. No LLM is called and no CRM is
        read; the templates and stages come from this workflow's own vocabulary.
      </p>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Account" id="wf130-account" hint="The buyer organisation.">
          <input
            id="wf130-account"
            className={inputClass}
            value={account}
            onChange={(event) => setAccount(event.target.value)}
          />
        </Field>
        <Field label="Contact" id="wf130-contact" hint="The person the collateral greets.">
          <input
            id="wf130-contact"
            className={inputClass}
            value={contact}
            onChange={(event) => setContact(event.target.value)}
          />
        </Field>
        <Field label="Product" id="wf130-product" hint="What is being sold.">
          <input
            id="wf130-product"
            className={inputClass}
            value={product}
            onChange={(event) => setProduct(event.target.value)}
          />
        </Field>
        <Field label="Deal stage" id="wf130-stage" hint="Hidden sections reveal at or past this stage.">
          <select
            id="wf130-stage"
            className={inputClass}
            value={stage}
            onChange={(event) => setStage(event.target.value)}
          >
            {stages.map((name) => (
              <option key={name} value={name}>
                {STAGE_LABELS[name] || name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Seller" id="wf130-seller" hint="The named point of contact.">
          <input
            id="wf130-seller"
            className={inputClass}
            value={seller}
            onChange={(event) => setSeller(event.target.value)}
          />
        </Field>
        <Field label="Template" id="wf130-template" hint="Repeat deals start from a template.">
          <select
            id="wf130-template"
            className={inputClass}
            value={templateKey}
            onChange={(event) => setTemplateKey(event.target.value)}
          >
            {templates.map((template) => (
              <option key={template.key} value={template.key}>
                {template.name} ({kindLabel(template.kind)})
              </option>
            ))}
          </select>
        </Field>
      </div>
      {refused.length > 0 && <InlineNote tone="warning">{refused.join(' ')}</InlineNote>}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="primary"
          icon="plus"
          disabled={busy || !roomId || refused.length > 0}
          onClick={submit}
        >
          Generate the collateral
        </Button>
        <span className="text-xs text-muted-foreground">
          {roomId ? 'The collateral belongs to the selected room.' : 'Pick a room first.'}
        </span>
      </div>
    </Card>
  )
}

function ReviewPanel({ collateral, assets, onSave, onPublish, onWithdraw, onRefresh, onClose }) {
  const [stage, setStage] = useState(collateral.stage)
  const [bodies, setBodies] = useState(() =>
    Object.fromEntries((collateral.sections || []).map((section) => [section.key, section.body])),
  )
  const [assetId, setAssetId] = useState(assets[0]?.id || '')
  const trail = useAsync(() => collateralApi.events(collateral.id), [collateral.id])
  const stages = Object.keys(STAGE_LABELS)

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-foreground">{collateral.title}</h2>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">
            {collateral.id} | {kindLabel(collateral.kind)} | v{collateral.version} |{' '}
            {collateral.template_key} | published {shortTimestamp(collateral.published_at)}
            {collateral.room_page_id ? ` | room page ${collateral.room_page_id}` : ''}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StateBadge state={collateral.state} />
          <KindBadge kind={collateral.kind} />
          <Button icon="close" onClick={onClose}>
            Close the review
          </Button>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Deal stage" id="wf130-review-stage" hint="Moving the stage reveals hidden sections.">
          <select
            id="wf130-review-stage"
            className={inputClass}
            value={stage}
            onChange={(event) => setStage(event.target.value)}
          >
            {stages.map((name) => (
              <option key={name} value={name}>
                {STAGE_LABELS[name] || name}
              </option>
            ))}
          </select>
        </Field>
        <div className="flex items-end gap-3">
          <Button
            variant="primary"
            icon="schema"
            disabled={collateral.state === 'withdrawn'}
            onClick={() =>
              onSave({
                deal: { stage },
                sections: (collateral.sections || []).map((section) => ({
                  key: section.key,
                  body: bodies[section.key],
                })),
              })
            }
          >
            Save the edits
          </Button>
          <Button
            icon="plus"
            disabled={collateral.state === 'withdrawn'}
            onClick={onPublish}
          >
            Publish to the room
          </Button>
          <Button
            variant="danger"
            icon="trash"
            disabled={collateral.state === 'withdrawn'}
            onClick={onWithdraw}
          >
            Withdraw
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <h3 className="text-base font-semibold text-foreground">Content blocks</h3>
        {(collateral.sections || []).map((section) => (
          <div key={section.key} className="flex flex-col gap-2 rounded-sm border border-border-subtle p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-medium text-foreground">{section.title}</span>
              <span className="font-mono text-xs text-muted-foreground">
                {section.key}
                {section.synced ? ` | synced from ${section.asset_ref} v${section.asset_version}` : ''}
              </span>
            </div>
            <Field
              label={`Body of ${section.title}`}
              id={`wf130-body-${section.key}`}
              hint="Editable. A token with no value is named below."
            >
              <textarea
                id={`wf130-body-${section.key}`}
                className={`${inputClass} min-h-20`}
                value={bodies[section.key] ?? ''}
                onChange={(event) =>
                  setBodies((current) => ({ ...current, [section.key]: event.target.value }))
                }
                rows={3}
              />
            </Field>
          </div>
        ))}
      </div>

      <SectionList sections={collateral.sections} />

      <div className="flex flex-col gap-3">
        <h3 className="text-base font-semibold text-foreground">Synced sections</h3>
        <p className="text-xs text-muted-foreground">
          A change to a source asset is pushed across every live artefact that draws from it.
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Source asset" id="wf130-asset" hint="The library entry to push.">
            <select
              id="wf130-asset"
              className={inputClass}
              value={assetId}
              onChange={(event) => setAssetId(event.target.value)}
            >
              {(assets || []).map((asset) => (
                <option key={asset.id} value={asset.id}>
                  {asset.title} (v{asset.version})
                </option>
              ))}
            </select>
          </Field>
          <div className="flex items-end">
            <Button icon="refresh" disabled={!assetId} onClick={() => onRefresh(assetId)}>
              Refresh the live collateral
            </Button>
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <h3 className="text-base font-semibold text-foreground">Trail</h3>
        {trail.loading && <p className="text-sm text-muted-foreground">Loading the trail.</p>}
        {!trail.loading && (trail.data?.events || []).length === 0 && (
          <p className="text-sm text-muted-foreground">No events recorded yet.</p>
        )}
        <ul className="flex flex-col">
          {(trail.data?.events || []).map((event, index) => (
            <li
              key={`${event.kind}-${index}`}
              className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle py-2 text-xs last:border-b-0"
            >
              <span className="text-foreground">{event.kind}</span>
              <span className="font-mono text-muted-foreground">{shortTimestamp(event.at)}</span>
            </li>
          ))}
        </ul>
      </div>
    </Card>
  )
}

function AssetsCard({ assets, onCreate, onRefresh }) {
  const [key, setKey] = useState('')
  const [kind, setKind] = useState('case_study')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)

  const refused = []
  if (!key.trim()) refused.push('A key is required.')

  async function submit() {
    setBusy(true)
    await onCreate({
      key: key.trim(),
      kind,
      title: title.trim() || key.trim(),
      body,
    })
    setBusy(false)
  }

  return (
    <Card className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold text-foreground">Content library</h2>
      <p className="text-xs text-muted-foreground">
        Register a case study, a deck or a pricing entry. A synced section draws its body from here,
        so a change here can be pushed into every live collateral.
      </p>
      {assets.length > 0 && (
        <ul className="flex flex-col">
          {assets.map((asset) => (
            <li
              key={asset.id}
              className="flex flex-wrap items-center justify-between gap-3 border-b border-border-subtle py-3 last:border-b-0"
            >
              <span className="flex min-w-0 flex-col">
                <span className="truncate text-sm text-foreground">{asset.title}</span>
                <span className="font-mono text-xs text-muted-foreground">
                  {asset.key} | {asset.kind} | v{asset.version} | updated{' '}
                  {shortTimestamp(asset.updated_at)}
                </span>
              </span>
              <Button icon="refresh" onClick={() => onRefresh(asset)}>
                Push to live collateral
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Key" id="wf130-asset-key" hint="How a template names this asset.">
          <input
            id="wf130-asset-key"
            className={inputClass}
            value={key}
            onChange={(event) => setKey(event.target.value)}
          />
        </Field>
        <Field label="Kind" id="wf130-asset-kind" hint="Case study, pricing or proof.">
          <input
            id="wf130-asset-kind"
            className={inputClass}
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          />
        </Field>
        <Field label="Title" id="wf130-asset-title" hint="How the asset reads to a seller.">
          <input
            id="wf130-asset-title"
            className={inputClass}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </Field>
      </div>
      <Field label="Body" id="wf130-asset-body" hint="May carry personalisation tokens like {{account}}.">
        <textarea
          id="wf130-asset-body"
          className={`${inputClass} min-h-20`}
          value={body}
          onChange={(event) => setBody(event.target.value)}
          rows={3}
        />
      </Field>
      {refused.length > 0 && <InlineNote tone="warning">{refused.join(' ')}</InlineNote>}
      <div className="flex flex-wrap items-center gap-3">
        <Button icon="plus" disabled={busy || refused.length > 0} onClick={submit}>
          Register the asset
        </Button>
        <span className="text-xs text-muted-foreground">
          {assets.length} asset(s) in the library.
        </span>
      </div>
    </Card>
  )
}

export default {
  id: 'wf-130-collateral-generation',
  label: 'Collateral generation',
  icon: 'audit',
  order: 1300,
  Component: CollateralGenerationPage,
}
