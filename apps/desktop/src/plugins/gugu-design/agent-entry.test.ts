import { describe, expect, it, vi } from 'vitest'

import { planAiEntry, routeFromHash, runAiEntry } from './agent-entry'

describe('routeFromHash', () => {
  it.each([
    ['', '/'],
    ['#', '/'],
    ['#/', '/'],
    ['#/design', '/design'],
    ['#/design?pid=abc', '/design'],
    ['#/settings?tab=providers', '/settings'],
    ['#design', '/design']
  ])('normalises %j to %j', (hash, expected) => {
    expect(routeFromHash(hash)).toBe(expected)
  })
})

describe('planAiEntry', () => {
  it('uses the existing design composer when a project is open', () => {
    expect(planAiEntry({ hasDocument: true, hash: '#/design?pid=abc' })).toEqual({
      target: 'design-composer',
      navigateTo: null
    })
  })

  it('falls back to the global composer on the design project picker', () => {
    expect(planAiEntry({ hasDocument: false, hash: '#/design' })).toEqual({
      target: 'global-composer',
      navigateTo: '/'
    })
  })

  it('uses the global composer on every other surface without navigating', () => {
    expect(planAiEntry({ hasDocument: false, hash: '#/settings?tab=providers' })).toEqual({
      target: 'global-composer',
      navigateTo: null
    })
    expect(planAiEntry({ hasDocument: true, hash: '' })).toEqual({
      target: 'global-composer',
      navigateTo: null
    })
  })
})

describe('runAiEntry', () => {
  it('focuses the design composer and leaves the route alone', () => {
    const focusGlobalComposer = vi.fn()
    const navigate = vi.fn()
    const target = runAiEntry({
      hash: () => '#/design',
      hasDocument: () => true,
      navigate,
      focusDesignComposer: () => true,
      focusGlobalComposer
    })
    expect(target).toBe('design-composer')
    expect(navigate).not.toHaveBeenCalled()
    expect(focusGlobalComposer).not.toHaveBeenCalled()
  })

  it('falls back to the global composer when the design field is not mounted', () => {
    const focusGlobalComposer = vi.fn()
    const target = runAiEntry({
      hash: () => '#/design',
      hasDocument: () => true,
      navigate: vi.fn(),
      focusDesignComposer: () => false,
      focusGlobalComposer
    })
    expect(target).toBe('global-composer')
    expect(focusGlobalComposer).toHaveBeenCalledTimes(1)
  })

  it('navigates home before focusing the global composer from the project picker', () => {
    const calls: string[] = []
    const target = runAiEntry({
      hash: () => '#/design',
      hasDocument: () => false,
      navigate: path => calls.push(`navigate:${path}`),
      focusDesignComposer: () => false,
      focusGlobalComposer: () => calls.push('focus:global')
    })
    expect(target).toBe('global-composer')
    expect(calls).toEqual(['navigate:/', 'focus:global'])
  })
})
