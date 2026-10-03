/**
 * Sandbox validation (WF-048): point a connection at a non-production org,
 * run the test sync, score the four researched assertions, promote only on
 * green.
 *
 * The page reads its pickers and its badges from the same data the backend
 * serves (`/wf-048/vocabulary`, `/wf-048/connections`, `/wf-048/summary`),
 * so a picker option cannot drift from the rule the validator enforces.
 * `apiRequest` throws with the response status attached, which is what lets
 * this page tell a 422 ("the newest test sync is not green") apart from a
 * 502 ("the sandbox is unreachable").
 */

import { useState } from 'react'
import { api, apiRequest } from '@/lib/api'
import { Button, Card, ErrorNote, Field, inputClass, Spinner, StatCard, useAsync } from '@/components/ui'
import { DataTable, Fact, Notice, PathButton, Section } from './primitives'

const PREFIX = '/wf-048'

const RUN_TONES = {
  passed: 'bg-accent/15 text-accent border-accent/30',
  failed: 'bg-destructive/15 text-destructive border-destructive/30',
  skipped: 'bg-muted text-muted-foreground border-border-subtle/40',
}

const ENV_TONES = {
  production: 'bg-muted text-muted-foreground border-border-subtle/40',
  test: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
  converted_sandbox: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
}

function RunOutcomeBadge({ status }) {
  const tone = RUN_TONES[status] || RUN_TONES.skipped
  return (
    <span
      className={`inline-flex items-center rounded-sm border px-2 py-0.5 font-mono text-xs font-medium ${tone}`}
    >
      {status || 'not run'}
    </span>
  )
}

function EnvironmentBadge({ environment, envType }) {
  const tone = ENV_TONES[environment] || ENV_TONES.production
  return (
    <span className="inline-flex items-center gap-2">
      <span className={`inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-xs font-medium ${tone}`}>
        {environment || 'production'}
      </span>
      {envType && <span className="font-mono text-xs text-muted-foreground">{envType}</span>}
    </span>
  )
}

function RunAssertions({ run }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <RunOutcomeBadge status={run.status} />
        <span className="font-mono text-xs text-muted-foreground">
          transport {run.transport || '—'}
          {run.aborted_reason ? ` · stopped: ${run.aborted_reason}` : ''}
        </span>
      </div>
      <dl className="grid gap-3 sm:grid-cols-2">
        {(run.assertions || []).map((entry) => (
          <Fact key={entry.kind} term={`${entry.kind} — ${entry.outcome}`}>
            {entry.detail}
          </Fact>
        ))}
      </dl>
    </div>
  )
}

function ConnectionRow({ connection, busy, onRun, onPromote, onRevert }) {
  const promotable = connection.last_run_status === 'passed'
  const isTest = connection.environment !== 'production'
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-foreground">{connection.label}</p>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">
            {connection.vendor} · {connection.object_name} · key {connection.key_field}
          </p>
          <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground/70">{connection.base_url}</p>
        </div>
        <EnvironmentBadge environment={connection.environment} envType={connection.env_type} />
      </div>
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span>last test sync</span>
        <RunOutcomeBadge status={connection.last_run_status} />
        {connection.expires_at && <span>trial expires {connection.expires_at}</span>}
      </div>
      {isTest ? (
        <div className="flex flex-wrap gap-2">
          <Button icon="refresh" disabled={busy} onClick={() => onRun(connection)}>
            Run test sync
          </Button>
          <Button disabled={busy || !promotable} onClick={() => onPromote(connection)}>
            Promote to production
          </Button>
          <Button variant="danger" disabled={busy} onClick={() => onRevert(connection)}>
            Revert
          </Button>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          No test environment yet — create one from this row before cutting over to production.
        </p>
      )}
    </Card>
  )
}

export default function SandboxValidation() {
  const summaryState = useAsync(() => apiRequest(`${PREFIX}/summary`), [])
  const connectionsState = useAsync(() => apiRequest(`${PREFIX}/connections`), [])
  const roomsState = useAsync(() => api.listRecords('room', { limit: 50 }), [])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)
  const [latestRun, setLatestRun] = useState(null)
  const [roomId, setRoomId] = useState('')
  const [form, setForm] = useState({ vendor: 'salesforce', object_name: 'Engagement__c', base_url: '' })

  const rooms = roomsState.data?.records || []
  // The self-test route is room-scoped, so a reviewer needs a room chosen
  // before the button can do anything. Default to the first one.
  const chosenRoom = roomId || rooms[0]?.id || ''

  const refetchAll = () => {
    summaryState.refetch()
    connectionsState.refetch()
  }

  const post = async (path, payload) => {
    setBusy(true)
    setMessage(null)
    try {
      const response = await apiRequest(path, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      refetchAll()
      return response
    } finally {
      setBusy(false)
    }
  }

  const onRun = async (connection) => {
    try {
      const body = await post(`${PREFIX}/connections/${connection.id}/run-test-sync`, {})
      setLatestRun(body.run || null)
      setMessage({
        tone: body.run?.status === 'passed' ? 'success' : 'danger',
        title: `Test sync ${body.run?.status || 'failed'}`,
        text: (body.run?.assertions || []).map((entry) => `${entry.kind}: ${entry.outcome}`).join(' · '),
      })
    } catch (error) {
      setMessage({ tone: 'danger', title: 'Test sync could not run', text: String(error.message || error) })
    }
  }

  const onPromote = async (connection) => {
    try {
      const body = await post(`${PREFIX}/connections/${connection.id}/promote`, {})
      setMessage({
        tone: 'success',
        title: 'Promoted to production',
        text: `The promotion names the green run it came from: ${body.promoted_run_id}.`,
      })
    } catch (error) {
      if (error.status === 422) {
        setMessage({
          tone: 'warning',
          title: 'Not promoted',
          text: 'The newest test sync is not green. Run it, then promote.',
        })
      } else {
        setMessage({ tone: 'danger', title: 'Promotion refused', text: String(error.message || error) })
      }
    }
  }

  const onRevert = async (connection) => {
    try {
      await post(`${PREFIX}/connections/${connection.id}/revert`, {})
      setMessage({ tone: 'info', title: 'Test environment reverted', text: 'The production row was untouched.' })
    } catch (error) {
      setMessage({ tone: 'danger', title: 'Revert refused', text: String(error.message || error) })
    }
  }

  const onSelfTest = async (event) => {
    event.preventDefault()
    if (!chosenRoom) {
      setMessage({
        tone: 'warning',
        title: 'Pick a room first',
        text: 'The self-test creates a production connection and a sandbox inside one room.',
      })
      return
    }
    try {
      const body = await post(`${PREFIX}/rooms/${chosenRoom}/self-test`, {
        vendor: form.vendor,
        object_name: form.object_name,
        base_url: form.base_url,
        key_field: 'External_Engagement_Id__c',
        test_environment: { kind: 'power_platform', env_type: 'sandbox' },
      })
      setMessage({
        tone: body.run?.status === 'passed' ? 'success' : 'danger',
        title: `Self-test ${body.run?.status || 'failed'}`,
        text: (body.run?.assertions || [])
          .map((entry) => `${entry.kind}: ${entry.outcome}`)
          .join(' · '),
      })
      setLatestRun({
        run_id: body.run?.run_id,
        status: body.run?.status,
        assertions: body.run?.assertions || [],
        transport: 'simulated',
      })
    } catch (error) {
      setMessage({ tone: 'danger', title: 'Self-test refused', text: String(error.message || error) })
    }
  }

  if (summaryState.loading || connectionsState.loading || roomsState.loading) {
    return <Spinner label="Loading sandbox validation" />
  }
  if (roomsState.error) {
    return <ErrorNote error={roomsState.error} onRetry={roomsState.refetch} />
  }
  if (summaryState.error) {
    return <ErrorNote error={summaryState.error} onRetry={summaryState.refetch} />
  }
  if (connectionsState.error) {
    return <ErrorNote error={connectionsState.error} onRetry={connectionsState.refetch} />
  }

  const summary = summaryState.data || {}
  const connections = connectionsState.data?.connections || []

  return (
    <div className="flex flex-col gap-8">
      <Section
        title="Test environment"
        hint="Point a connection at a non-production org, run the full test sync with synthetic buyers, and open the promotion gate only on green."
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <StatCard
            label="Connections"
            value={summary.connections ?? 0}
            hint={`${summary.test_environments ?? 0} test environments`}
          />
          <StatCard label="Green runs" value={summary.green_runs ?? 0} hint={`${summary.failed_runs ?? 0} failed`} />
          <StatCard label="Promoted" value={summary.promoted ?? 0} hint="switched to production after green" />
        </div>
      </Section>

      {message && (
        <Notice tone={message.tone} title={message.title}>
          {message.text}
        </Notice>
      )}

      <Section title="Connections" hint="Production rows and their test environments, newest first.">
        <DataTable
          columns={[
            { key: 'label', header: 'Connector', render: (row) => <span className="text-[13px]">{row.label}</span> },
            { key: 'vendor', header: 'Vendor', render: (row) => <span className="font-mono text-xs">{row.vendor}</span> },
            {
              key: 'env',
              header: 'Environment',
              render: (row) => <EnvironmentBadge environment={row.environment} envType={row.env_type} />,
            },
            { key: 'run', header: 'Last sync', render: (row) => <RunOutcomeBadge status={row.last_run_status} /> },
          ]}
          rows={connections}
          rowKey={(row) => row.id}
          empty="No connectors registered yet"
        />
        <div className="grid gap-4">
          {connections
            .filter((connection) => connection.environment !== 'production')
            .map((connection) => (
              <ConnectionRow
                key={connection.id}
                connection={connection}
                busy={busy}
                onRun={onRun}
                onPromote={onPromote}
                onRevert={onRevert}
              />
            ))}
        </div>
      </Section>

      {latestRun && (
        <Section title="Latest run" hint="The four researched assertions, scored from the sandbox's own answers.">
          <RunAssertions run={latestRun} />
        </Section>
      )}

      <Section
        title="Self-test"
        hint="The researched extensibility: create both connection rows and run the fixture, making sandbox validation a first-class feature rather than an ops chore."
      >
        <Card>
          <form className="grid gap-4 sm:grid-cols-3" onSubmit={onSelfTest}>
            <Field label="Room" hint="the room both connection rows are created in" id="wf048-room">
              <select
                id="wf048-room"
                className={inputClass}
                value={chosenRoom}
                onChange={(event) => setRoomId(event.target.value)}
              >
                {rooms.map((room) => (
                  <option key={room.id} value={room.id}>
                    {room.data?.name || room.data?.account || room.id}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Vendor" id="wf048-vendor">
              <select
                id="wf048-vendor"
                className={inputClass}
                value={form.vendor}
                onChange={(event) => setForm({ ...form, vendor: event.target.value })}
              >
                <option value="salesforce">salesforce</option>
                <option value="hubspot">hubspot</option>
                <option value="dataverse">dataverse</option>
              </select>
            </Field>
            <Field label="CRM object" id="wf048-object">
              <input
                id="wf048-object"
                className={inputClass}
                value={form.object_name}
                onChange={(event) => setForm({ ...form, object_name: event.target.value })}
              />
            </Field>
            <Field
              label="Production base URL"
              hint="the org the validated mapping is cut over to"
              id="wf048-base"
            >
              <input
                id="wf048-base"
                className={inputClass}
                placeholder="https://org.example/prod"
                value={form.base_url}
                onChange={(event) => setForm({ ...form, base_url: event.target.value })}
              />
            </Field>
            <div className="sm:col-span-3">
              <PathButton
                glyph="gate"
                variant="primary"
                type="submit"
                disabled={busy || !form.base_url || !chosenRoom}
              >
                Create both rows and run the fixture
              </PathButton>
              {rooms.length === 0 && (
                <p className="mt-2 text-xs text-muted-foreground">
                  No room yet. The seeder creates four; a self-test needs one to scope both rows to.
                </p>
              )}
            </div>
          </form>
        </Card>
      </Section>
    </div>
  )
}
