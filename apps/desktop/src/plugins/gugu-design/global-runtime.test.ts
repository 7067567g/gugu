/**
 * M3b 全局 runtime 测试 —— 导航四界面 + 全局引导零残留。
 *
 * 这里不 mock guidance manager 内部：overlay 真挂到 document.body，断言
 * overlay/target/listener/timer 全部归零。导航只 mock 跨进程的 REST 读取与
 * `host.navigate`，其余（screen→route 表、ack 发布、去重、过期）都走真实代码。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AgentState } from './agent-state'
import type { GuidanceCommand, NavigationCommand } from './api'
import { createGuidanceLayer, type GuidanceLayer } from './guidance'
import { NAVIGATION_ROUTES, routeForScreen, startGlobalRuntime, type GlobalRuntime } from './global-runtime'

let runtime: GlobalRuntime | null = null
let layer: GuidanceLayer
let guidanceCommand: GuidanceCommand | null = null
let navigationCommand: NavigationCommand | null = null
let hash = '#/design'
let publishes: AgentState[] = []
let acked: string[] = []
let dismissed: string[] = []
let intervalCount = 0

function mountAnchor(anchor: string): HTMLElement {
  const element = document.createElement('button')
  element.setAttribute('data-agent-id', anchor)
  document.body.appendChild(element)
  return element
}

async function flush(): Promise<void> {
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
}

function boot(overrides: { navigate?: (path: string) => void } = {}): GlobalRuntime {
  layer = createGuidanceLayer({
    document,
    window,
    idFactory: () => `gugu-global-${++intervalCount}`
  })
  runtime = startGlobalRuntime({
    acknowledgeNavigation: id => {
      acked.push(id)
    },
    dismissGuidance: id => {
      dismissed.push(id)
    },
    fetchGuidance: async () => guidanceCommand,
    fetchNavigation: async () => navigationCommand,
    guidanceIntervalMs: 999_999,
    hash: () => hash,
    layer,
    navigate:
      overrides.navigate ??
      (path => {
        hash = `#${path}`
      }),
    navigationIntervalMs: 999_999,
    now: () => 1000,
    project: () => ({ pid: null, selection: [] }),
    publishAgentState: state => {
      publishes.push(state)
    },
    subscribeView: () => () => {},
    window
  })
  return runtime
}

beforeEach(() => {
  document.body.innerHTML = ''
  hash = '#/design'
  guidanceCommand = null
  navigationCommand = null
  publishes = []
  acked = []
  dismissed = []
  intervalCount = 0
  vi.useFakeTimers()
})

afterEach(() => {
  runtime?.stop()
  runtime = null
  vi.useRealTimers()
  document.body.innerHTML = ''
})

describe('screen → route table', () => {
  it.each([
    ['work', '/'],
    ['design', '/design'],
    ['settings', '/settings'],
    ['artifacts', '/artifacts']
  ] as const)('%s lands on %s', (screen, route) => {
    expect(NAVIGATION_ROUTES[screen]).toBe(route)
    expect(routeForScreen(screen)).toBe(route)
  })
})

describe('navigate', () => {
  it.each(['work', 'design', 'settings', 'artifacts'] as const)(
    'navigates to %s and acknowledges the exact request id',
    async screen => {
      let landed: string | null = null
      boot({
        navigate: path => {
          landed = path
          hash = `#${path}`
        }
      })
      await flush()
      publishes = []

      navigationCommand = {
        created_at: 1000,
        expires_at: 2000,
        request_id: `nav-${screen}`,
        route: NAVIGATION_ROUTES[screen],
        screen
      }
      await runtime!.tickNavigation()

      expect(landed).toBe(NAVIGATION_ROUTES[screen])
      expect(acked).toEqual([`nav-${screen}`])
      expect(publishes.at(-1)).toMatchObject({
        nav_request_id: `nav-${screen}`,
        route: NAVIGATION_ROUTES[screen],
        screen,
        surface: screen
      })
    }
  )

  it('ignores the same request id twice and expired requests', async () => {
    const navigate = vi.fn()
    boot({ navigate })
    await flush()

    navigationCommand = {
      created_at: 1000,
      expires_at: 2000,
      request_id: 'nav-once',
      route: '/settings',
      screen: 'settings'
    }
    await runtime!.tickNavigation()
    await runtime!.tickNavigation()
    expect(navigate).toHaveBeenCalledTimes(1)

    navigationCommand = {
      created_at: 1,
      expires_at: 2,
      request_id: 'nav-stale',
      route: '/artifacts',
      screen: 'artifacts'
    }
    await runtime!.tickNavigation()
    expect(navigate).toHaveBeenCalledTimes(1)
    expect(acked).toContain('nav-stale')
  })

  it('publishes a debounced agent-state with the pending nav id after hashchange', async () => {
    boot()
    await flush()
    publishes = []
    navigationCommand = {
      created_at: 1000,
      expires_at: 2000,
      request_id: 'nav-debounce',
      route: '/artifacts',
      screen: 'artifacts'
    }
    await runtime!.tickNavigation()
    publishes = []

    window.dispatchEvent(new HashChangeEvent('hashchange'))
    vi.advanceTimersByTime(250)
    await flush()

    expect(publishes.at(-1)?.nav_request_id).toBe('nav-debounce')
    expect(publishes.at(-1)?.route).toBe('/artifacts')
  })
})

describe('global guidance', () => {
  it.each(['highlight', 'box', 'label'] as const)(
    'renders %s on the work surface and leaves nothing behind on stop',
    async style => {
      boot()
      await flush()
      const target = mountAnchor('work.composer')
      guidanceCommand = {
        guidance_id: `gd-work-${style}`,
        style,
        target_anchor: 'work.composer',
        text: 'AI 输入框在这里'
      }

      await runtime!.tickGuidance()

      expect(layer.activeId()).toBe(`gd-work-${style}`)
      expect(target.getAttribute('data-gugu-guide-target')).toBe(`gd-work-${style}`)
      if (style === 'label') {
        expect(document.querySelector('[data-gugu-guide-label]')?.textContent).toBe('AI 输入框在这里')
      } else {
        expect(document.querySelector(`[data-gugu-guide-frame="${style}"]`)).not.toBeNull()
      }

      runtime!.stop()
      runtime = null
      expect(document.querySelectorAll('[data-gugu-guide]')).toHaveLength(0)
      expect(document.querySelectorAll('[data-gugu-guide-target]')).toHaveLength(0)
    }
  )

  it('highlights an anchor on a non-design surface and clears to zero residue', async () => {
    boot()
    await flush()
    const target = mountAnchor('artifacts.root')
    guidanceCommand = {
      guidance_id: 'gd-global-1',
      style: 'label',
      target_anchor: 'artifacts.root',
      text: '产物都在这里'
    }

    await runtime!.tickGuidance()

    expect(layer.activeId()).toBe('gd-global-1')
    expect(document.querySelectorAll('[data-gugu-guide]')).toHaveLength(1)
    expect(target.getAttribute('data-gugu-guide-target')).toBe('gd-global-1')

    // Route switch is a hard interrupt even though the poller is global.
    window.dispatchEvent(new HashChangeEvent('hashchange'))
    expect(layer.activeId()).toBeNull()
    expect(document.querySelectorAll('[data-gugu-guide]')).toHaveLength(0)
    expect(document.querySelectorAll('[data-gugu-guide-target]')).toHaveLength(0)
  })

  it('disposes the layer and every timer on stop', async () => {
    boot()
    await flush()
    mountAnchor('settings.header')
    guidanceCommand = {
      guidance_id: 'gd-stop',
      target_anchor: 'settings.header',
      text: '设置在这里',
      timeout_ms: 60_000
    }
    await runtime!.tickGuidance()
    expect(document.querySelectorAll('[data-gugu-guide]')).toHaveLength(1)

    runtime!.stop()
    runtime = null

    expect(document.querySelectorAll('[data-gugu-guide]')).toHaveLength(0)
    expect(document.querySelectorAll('[data-gugu-guide-target]')).toHaveLength(0)
    expect(layer.stats()).toEqual({ listeners: 0, marked_targets: 0, overlays: 0, timers: 0 })
    expect(vi.getTimerCount()).toBe(0)
  })

  it('dismisses a guidance whose anchor is not on the current surface', async () => {
    boot()
    await flush()
    guidanceCommand = { guidance_id: 'gd-missing', target_anchor: 'artifacts.root', text: 'x' }

    await runtime!.tickGuidance()

    expect(layer.activeId()).toBeNull()
    expect(document.querySelectorAll('[data-gugu-guide]')).toHaveLength(0)
    expect(dismissed).toContain('gd-missing')
  })
})
