import { describe, expect, it } from 'vitest'

import { buildAgentState, surfaceForRoute } from './agent-state'

describe('surfaceForRoute', () => {
  it.each([
    ['/', 'work'],
    ['/chat', 'work'],
    ['/design', 'design'],
    ['/design/settings', 'design'],
    ['/settings', 'settings'],
    ['/settings?tab=providers', 'settings'],
    ['/kanban', 'other']
  ] as const)('maps %s to %s', (route, expected) => {
    expect(surfaceForRoute(route)).toBe(expected)
  })
})

describe('buildAgentState', () => {
  it('publishes the current project and a snapshot of the selection', () => {
    const selection = ['agent-one', 'agent-two']
    const state = buildAgentState({ hash: '#/design?pid=proj-1', now: 1234, pid: 'proj-1', selection })

    expect(state).toEqual({
      project_id: 'proj-1',
      route: '/design',
      selection: ['agent-one', 'agent-two'],
      surface: 'design',
      updated_at: 1234
    })

    selection.push('later')
    expect(state.selection).toEqual(['agent-one', 'agent-two'])
  })

  it('publishes a null project when no document is open', () => {
    const state = buildAgentState({ hash: '', now: 5, pid: null, selection: [] })
    expect(state.project_id).toBeNull()
    expect(state.surface).toBe('work')
    expect(state.route).toBe('/')
  })
})
