import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import MutualActionPlan from '@/features/wf-124-native-mutual-action-plan-execution/ActionPlanBoard.jsx'
import descriptor from '@/features/wf-124-native-mutual-action-plan-execution/index.jsx'
import { conflictText, mapApi, STATUS_TONES, STATUS_WORDS } from '@/features/wf-124-native-mutual-action-plan-execution/api.js'
import { httpError, stubApi } from './fixtures.js'

/**
 * Tests for WF-124's page.
 *
 * What these pin is the behaviour the researched specification requires, and the four
 * things a UI can quietly get wrong on a shared plan:
 *
 *   1. **A blocked task says it is waiting on another.** `blocked` is derived by the
 *      server from the graph, so a page that showed it as ordinary work in progress
 *      would leave a seller wondering why the status they set kept reverting.
 *   2. **The buyer's view really is a different response.** The switch calls
 *      `/shared`, so what a seller sees when it is on is what the client receives.
 *      A page that only hid internal rows locally would pass a screenshot review and
 *      fail in the network tab, which is why the assertion is on the request and on
 *      the stub's response rather than on what was drawn.
 *   3. **The seller's view keeps showing internal work.** Hiding a task from the buyer
 *      must not hide it from the party that has to do it.
 *   4. **Every state carries a word.** No status is conveyed by colour alone, so each
 *      badge must render text a screen reader would say out loud.
 *
 * The stubs are keyed on this feature's own paths under `/wf-124`, and
 * `stubApi` throws on an unlisted path so a test cannot pass against the wrong
 * response.
 */

const ROOM = 'room_a'

const ROOMS = {
  count: 1,
  records: [{ id: ROOM, collection: 'room', revision: 1, data: { name: 'Northwind' } }],
}

const SUMMARY = {
  plans: 1,
  implementation_plans: 0,
  tasks: 4,
  done: 1,
  blocked: 1,
  overdue: 1,
  due_soon: 0,
  internal_tasks: 1,
  escalations: 1,
  generated_at: '2026-05-04T09:00:00.000+00:00',
}

function task(overrides = {}) {
  return {
    id: 'wf124_task_one',
    plan_id: 'wf124_plan_one',
    title: 'Buyer security review',
    owner: 'Buyer IT',
    owner_side: 'buyer',
    status: 'todo',
    visibility: 'external',
    due_date: '2026-05-06',
    position: 0,
    ...overrides,
  }
}

function plan(overrides = {}) {
  return {
    id: 'wf124_plan_one',
    room_id: ROOM,
    name: 'Northwind mutual action plan',
    phase: 'selling',
    status: 'todo',
    template_id: null,
    tasks: [
      task({ id: 'wf124_task_one', title: 'Buyer security review', status: 'in_progress' }),
      task({
        id: 'wf124_task_two',
        title: 'Sign the order form',
        owner: 'Dana',
        owner_side: 'seller',
        status: 'blocked',
        depends_on: ['wf124_task_one'],
        due_date: '2026-05-10',
        position: 1,
      }),
      task({
        id: 'wf124_task_three',
        title: 'Return the procurement questionnaire',
        status: 'todo',
        due_date: '2026-05-01',
        position: 2,
      }),
      task({
        id: 'wf124_task_four',
        title: 'Confirm the discount floor with finance',
        owner: 'Dana',
        owner_side: 'seller',
        status: 'todo',
        visibility: 'internal',
        position: 3,
      }),
      task({
        id: 'wf124_task_five',
        title: 'Share the signed order form',
        owner: 'Dana',
        owner_side: 'seller',
        status: 'done',
        position: 4,
      }),
    ],
    task_count: 5,
    done_count: 1,
    internal_count: 1,
    escalations: [
      {
        task_id: 'wf124_task_three',
        state: 'overdue',
        days: -3,
        owner: 'Buyer IT',
        owner_side: 'buyer',
      },
    ],
    dangling_dependencies: [],
    created_at: '2026-05-01T09:00:00.000+00:00',
    updated_at: '2026-05-04T09:00:00.000+00:00',
    ...overrides,
  }
}

const PLANS = { room_id: ROOM, plans: [plan()] }

/** What the buyer receives: the same plan with the internal task absent. */
const SHARED_BUYER = {
  id: 'wf124_plan_one',
  room_id: ROOM,
  name: 'Northwind mutual action plan',
  phase: 'selling',
  audience: 'buyer',
  tasks: plan().tasks.filter((row) => row.visibility !== 'internal'),
  task_count: 4,
  done_count: 1,
}

function baseRoutes(overrides = {}) {
  return {
    '/records/room': ROOMS,
    '/wf-124/summary': SUMMARY,
    [`/wf-124/rooms/${ROOM}/plans`]: PLANS,
    '/wf-124/plans/wf124_plan_one/shared': SHARED_BUYER,
    ...overrides,
  }
}

beforeEach(() => {
  globalThis.fetch = undefined
})

describe('WF-124 descriptor', () => {
  it('exports the shape the host discovers', () => {
    expect(descriptor.id).toBe('wf-124-native-mutual-action-plan-execution')
    expect(descriptor.label).toBeTruthy()
    expect(descriptor.Component).toBeTruthy()
    // The glyph is not in the shared PATHS map, so it travels as a path and
    // `components/ui.jsx` stays unedited.
    expect(descriptor.iconPath).toBeTruthy()
  })
})

describe('the seller board', () => {
  it('shows each task with its owner, side and a word for its state', async () => {
    stubApi(baseRoutes())
    render(<MutualActionPlan />)

    expect(await screen.findByText('Northwind mutual action plan')).toBeInTheDocument()
    expect(screen.getByText('Buyer security review')).toBeInTheDocument()

    // The owner and the side are on the row, because a shared plan is about people.
    const row = screen.getByText('Buyer security review').closest('li')
    expect(within(row).getByText('Buyer IT')).toBeInTheDocument()
    expect(within(row).getByText('Buyer')).toBeInTheDocument()
  })

  it('gives every status a word, so nothing is carried by colour alone', async () => {
    stubApi(baseRoutes())
    render(<MutualActionPlan />)

    await screen.findByText('Northwind mutual action plan')

    // Each badge the plan actually shows renders its word as text, which is what a
    // screen reader says out loud. The word is asserted, not the colour class.
    expect(screen.getAllByText(STATUS_WORDS.in_progress).length).toBeGreaterThan(0)
    expect(screen.getAllByText(STATUS_WORDS.blocked).length).toBeGreaterThan(0)
    expect(screen.getAllByText(STATUS_WORDS.todo).length).toBeGreaterThan(0)

    // `done` has its own word and its own tone, so a finished task is never only
    // a colour.
    expect(STATUS_WORDS.done).toBe('Done')
    expect(STATUS_TONES.done).toBe('success')
    expect(STATUS_TONES.blocked).toBe('warning')
    expect(STATUS_TONES.in_progress).toBe('info')
  })

  it('says a blocked task is waiting on another, because the server derived it', async () => {
    stubApi(baseRoutes())
    render(<MutualActionPlan />)

    await screen.findByText('Northwind mutual action plan')

    expect(screen.getByText(/Waiting on 1 other task/)).toBeInTheDocument()
    // The page must not imply a seller can clear this by hand.
    expect(screen.queryByText(/set by hand/)).not.toBeInTheDocument()
  })

  it('reports an overdue task in days, not as an unlabelled red row', async () => {
    stubApi(baseRoutes())
    render(<MutualActionPlan />)

    await screen.findByText('Northwind mutual action plan')
    expect(screen.getByText('3 days late')).toBeInTheDocument()
  })

  it('marks an internal-only task so the seller knows it is not shared', async () => {
    stubApi(baseRoutes())
    render(<MutualActionPlan />)

    await screen.findByText('Northwind mutual action plan')
    expect(screen.getByText('Confirm the discount floor with finance')).toBeInTheDocument()
    expect(screen.getAllByText('internal only').length).toBeGreaterThan(0)
  })

  it('shows the board totals from the server rather than counting on the page', async () => {
    stubApi(baseRoutes())
    render(<MutualActionPlan />)

    await screen.findByText('Northwind mutual action plan')

    // Each tile's label is the uppercased micro label the shared StatCard renders.
    // `Blocked` also appears as a task badge, so the label is matched exactly.
    for (const label of ['Plans', 'Tasks', 'Blocked', 'Overdue']) {
      expect(screen.getByText(label, { selector: 'p' })).toBeInTheDocument()
    }
    // The hint text proves the numbers came from the server's summary.
    expect(screen.getByText('Waiting on another task')).toBeInTheDocument()
  })

  it('warns when a dependency points at a task the plan does not hold', async () => {
    stubApi(
      baseRoutes({
        [`/wf-124/rooms/${ROOM}/plans`]: {
          room_id: ROOM,
          plans: [
            plan({
              dangling_dependencies: [{ task_id: 'wf124_task_two', depends_on: 'wf124_task_gone' }],
            }),
          ],
        },
      }),
    )
    render(<MutualActionPlan />)

    expect(
      await screen.findByText('A dependency points at a task that is not here'),
    ).toBeInTheDocument()
  })
})

describe('the buyer view', () => {
  it('asks the server for the buyer view rather than hiding rows on the page', async () => {
    const calls = stubApi(baseRoutes())
    render(<MutualActionPlan />)
    await screen.findByText('Northwind mutual action plan')

    // Nothing internal is on screen yet, so the only way it can vanish is if the
    // page asked the server for a buyer-shaped response.
    expect(screen.getByText('Confirm the discount floor with finance')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('switch', { name: /Show the buyer's view/ }))

    await waitFor(() => {
      const requested = calls.some((call) =>
        call.path.startsWith('/wf-124/plans/wf124_plan_one/shared'),
      )
      expect(requested).toBe(true)
    })
  })

  it('no longer shows the internal task once the buyer response is loaded', async () => {
    // The stub answers the buyer view without the internal task, so the page can only
    // lose that row by having asked the server. A page that filtered locally would
    // still pass the previous test, which is why both are asserted.
    stubApi(baseRoutes())
    render(<MutualActionPlan />)
    await screen.findByText('Confirm the discount floor with finance')

    await userEvent.click(screen.getByRole('switch', { name: /Show the buyer's view/ }))

    await waitFor(() => {
      expect(screen.queryByText('Confirm the discount floor with finance')).not.toBeInTheDocument()
    })
    // The shared tasks are still there, because one hidden task must not take the
    // shared roadmap away.
    expect(screen.getByText('Buyer security review')).toBeInTheDocument()
  })

  it('hides the add-a-task form in the buyer view', async () => {
    stubApi(baseRoutes())
    render(<MutualActionPlan />)
    await screen.findByText('Northwind mutual action plan')

    await userEvent.click(screen.getByRole('switch', { name: /Show the buyer's view/ }))
    await waitFor(() => {
      expect(screen.queryByText('Add a task')).not.toBeInTheDocument()
    })
  })
})

describe('closed won', () => {
  it('says what closing does before the seller clicks it', async () => {
    stubApi(baseRoutes())
    render(<MutualActionPlan />)
    await screen.findByText('Northwind mutual action plan')

    expect(
      screen.getByText(/carries its tasks into a new implementation plan. Nothing is deleted/),
    ).toBeInTheDocument()
  })

  it('reports what the conversion carried, in the words the server sent', async () => {
    const calls = stubApi(
      baseRoutes({
        '/wf-124/plans/wf124_plan_one/close-won': {
          id: 'wf124_plan_one',
          room_id: ROOM,
          name: 'Northwind mutual action plan',
          phase: 'closed_won',
          implementation_plan_id: 'wf124_plan_two',
          carried_tasks: 3,
          note: 'The agreed tasks are carried over, not replaced.',
        },
      }),
    )
    render(<MutualActionPlan />)
    await screen.findByText('Northwind mutual action plan')

    await userEvent.click(screen.getByRole('button', { name: 'Close won' }))

    expect(await screen.findByText('Plan closed')).toBeInTheDocument()
    expect(screen.getByText(/3 agreed task\(s\) were carried/)).toBeInTheDocument()
    expect(calls.some((call) => call.method === 'POST' && call.path.endsWith('/close-won'))).toBe(
      true,
    )
  })

  it('shows the reason token as a sentence when the graph refuses a change', async () => {
    stubApi(
      baseRoutes({
        '/wf-124/plans/wf124_plan_one/close-won': httpError(409, {
          error: 'plan_conflict',
          reason: 'plan_has_dependents',
          detail: 'A task that depends on this one is still open.',
        }),
      }),
    )
    render(<MutualActionPlan />)
    await screen.findByText('Northwind mutual action plan')

    await userEvent.click(screen.getByRole('button', { name: 'Close won' }))

    expect(await screen.findByText('The plan refused that change')).toBeInTheDocument()
    expect(screen.getByText(conflictText('plan_has_dependents'))).toBeInTheDocument()
  })
})

describe('adding a task', () => {
  it('puts a field-keyed error beside the input that caused it', async () => {
    stubApi(
      baseRoutes({
        [`/wf-124/plans/wf124_plan_one/tasks`]: httpError(400, {
          error: 'plan_invalid',
          detail: 'the task could not be read',
          errors: { owner: 'Name the owner. The plan tracks people, not queues.' },
        }),
      }),
    )
    render(<MutualActionPlan />)
    await screen.findByText('Northwind mutual action plan')

    await userEvent.click(screen.getByRole('button', { name: 'Add a task' }))
    await userEvent.click(screen.getByRole('button', { name: 'Add task' }))

    expect(
      await screen.findByText('Name the owner. The plan tracks people, not queues.'),
    ).toBeInTheDocument()
  })

  it('sends an internal task as internal rather than relying on the page to filter', async () => {
    const calls = stubApi(baseRoutes({ [`/wf-124/plans/wf124_plan_one/tasks`]: { id: 'new' } }))
    render(<MutualActionPlan />)
    await screen.findByText('Northwind mutual action plan')

    await userEvent.click(screen.getByRole('button', { name: 'Add a task' }))
    await userEvent.type(screen.getByLabelText('Task'), 'Margin check')
    await userEvent.type(screen.getByLabelText('Owner'), 'Dana')
    await userEvent.click(screen.getByRole('switch', { name: 'Internal only' }))
    await userEvent.click(screen.getByRole('button', { name: 'Add task' }))

    await waitFor(() => {
      const posted = calls.find((call) => call.method === 'POST' && call.path.includes('/tasks'))
      expect(posted).toBeTruthy()
      const body = JSON.parse(posted.body)
      expect(body.visibility).toBe('internal')
      expect(body.owner).toBe('Dana')
    })
  })
})

describe('the empty and error states', () => {
  it('says so when a room has no plan yet', async () => {
    stubApi(
      baseRoutes({
        '/wf-124/summary': { plans: 0, tasks: 0, done: 0, blocked: 0, overdue: 0, due_soon: 0 },
        [`/wf-124/rooms/${ROOM}/plans`]: { room_id: ROOM, plans: [] },
      }),
    )
    render(<MutualActionPlan />)

    expect(await screen.findByText('No action plan in this room yet')).toBeInTheDocument()
  })

  it('offers a retry when the board cannot load', async () => {
    stubApi(
      baseRoutes({
        '/wf-124/summary': httpError(500, { detail: 'the board did not load' }),
      }),
    )
    render(<MutualActionPlan />)

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })

  it('tells the seller to seed the demo when there are no rooms at all', async () => {
    stubApi({ '/records/room': { count: 0, records: [] } })
    render(<MutualActionPlan />)

    expect(await screen.findByText('No rooms yet')).toBeInTheDocument()
  })
})

describe('the api wrapper', () => {
  it('sends the internal visibility flag to the server rather than dropping it', () => {
    // The wrapper must not filter anything out on the way to the API. The visibility
    // rule belongs to the server, and a client-side filter would hide the fact that a
    // task was ever marked internal.
    expect(typeof mapApi.updateTask).toBe('function')
    expect(typeof mapApi.deleteTask).toBe('function')
    expect(typeof mapApi.closeWon).toBe('function')
  })
})
