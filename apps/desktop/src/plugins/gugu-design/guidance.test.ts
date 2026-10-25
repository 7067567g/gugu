/**
 * M2 引导层零残留测试。
 *
 * 每个中断源都走同一条断言：overlay DOM=0、target 标记=0、listener=0、
 * timer=0、焦点/滚动未被抢走、目标元素样式与 DOM 结构不变（布局不变）。
 * 这里用真实 DOM（jsdom）+ fake timers，不 mock manager 内部。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createGuidanceLayer, GuidanceError, type GuidanceLayer } from './guidance'

let layer: GuidanceLayer
let sequence = 0

function mountAnchor(anchor: string, tag = 'button'): HTMLElement {
  const element = document.createElement(tag)
  element.setAttribute('data-agent-id', anchor)
  element.textContent = anchor
  document.body.appendChild(element)
  return element
}

function freshLayer(overrides: Parameters<typeof createGuidanceLayer>[0] = {}): GuidanceLayer {
  return createGuidanceLayer({
    document,
    window,
    now: () => 1_790_000_000_000,
    idFactory: () => `gugu-test-${++sequence}`,
    ...overrides
  })
}

function expectZeroResidue(target?: HTMLElement): void {
  expect(document.querySelectorAll('[data-gugu-guide]')).toHaveLength(0)
  expect(document.querySelectorAll('[data-gugu-guide-target]')).toHaveLength(0)
  expect(layer.stats()).toEqual({ listeners: 0, marked_targets: 0, overlays: 0, timers: 0 })
  expect(vi.getTimerCount()).toBe(0)
  if (target) {
    expect(target.hasAttribute('data-gugu-guide-target')).toBe(false)
    expect(target.getAttribute('style')).toBeNull()
  }
}

function errorCode(run: () => unknown): string {
  try {
    run()
  } catch (error) {
    return (error as GuidanceError).code
  }
  return 'no-error'
}

beforeEach(() => {
  sequence = 0
  document.body.innerHTML = ''
  window.location.hash = ''
  vi.useFakeTimers()
  layer = freshLayer()
})

afterEach(() => {
  layer.dispose()
  vi.useRealTimers()
  document.body.innerHTML = ''
})

describe('render', () => {
  it('shows one overlay, marks the target and keeps it out of the target subtree', () => {
    const canvas = mountAnchor('design.canvas')
    const result = layer.show({ target_anchor: 'design.canvas', text: '产出会落在这里', style: 'highlight' })

    expect(result.guidance_id).toBe('gugu-test-1')
    const overlay = document.querySelector<HTMLElement>('[data-gugu-guide]')
    expect(overlay).not.toBeNull()
    expect(overlay?.getAttribute('data-agent-id')).toBe('guidance.overlay')
    expect(overlay?.style.position).toBe('fixed')
    expect(overlay?.style.pointerEvents).toBe('none')
    expect(overlay?.parentElement).toBe(document.body)
    expect(overlay?.querySelector('[data-gugu-guide-frame="highlight"]')).not.toBeNull()
    expect(overlay?.querySelector('[data-gugu-guide-label]')?.textContent).toBe('产出会落在这里')
    expect(canvas.getAttribute('data-gugu-guide-target')).toBe('gugu-test-1')
    expect(canvas.getAttribute('style')).toBeNull()
    expect(layer.getState()).toMatchObject({ status: 'active', guidance_id: 'gugu-test-1' })
  })

  it.each([
    ['box', '[data-gugu-guide-frame="box"]'],
    ['label', '[data-gugu-guide-label]']
  ] as const)('renders the %s style', (style, selector) => {
    mountAnchor('design.canvas')
    layer.show({ target_anchor: 'design.canvas', style, text: '看这里' })

    expect(document.querySelector(selector)).not.toBeNull()
    if (style === 'label') {
      expect(document.querySelector('[data-gugu-guide-frame]')).toBeNull()
    }
  })

  it('replaces an active guidance instead of stacking overlays', () => {
    const first = mountAnchor('design.canvas')
    const second = mountAnchor('canvas.export')
    layer.show({ target_anchor: 'design.canvas' })
    layer.show({ target_anchor: 'canvas.export' })

    expect(layer.activeId()).toBe('gugu-test-2')
    expect(document.querySelectorAll('[data-gugu-guide]')).toHaveLength(1)
    expect(first.hasAttribute('data-gugu-guide-target')).toBe(false)
    expect(second.getAttribute('data-gugu-guide-target')).toBe('gugu-test-2')
    expect(layer.stats()).toEqual({ listeners: 3, marked_targets: 1, overlays: 1, timers: 0 })
  })

  it('refuses missing and empty anchors without leaving residue', () => {
    mountAnchor('design.canvas')
    expect(errorCode(() => layer.show({ target_anchor: 'design.missing' }))).toBe('anchor_not_found')
    expect(errorCode(() => layer.show({ target_anchor: '   ' }))).toBe('invalid_arguments')
    expect(layer.getState().status).toBe('idle')
    expectZeroResidue()
  })
})

describe('interrupt sources', () => {
  const cases: Array<{
    expected: { interrupt: string; reason: string }
    fire: () => void
    name: string
  }> = [
    {
      name: 'ESC',
      fire: () => document.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Escape' })),
      expected: { interrupt: 'escape', reason: 'cancelled' }
    },
    {
      name: 'blank click',
      fire: () => {
        const blank = document.createElement('div')
        document.body.appendChild(blank)
        blank.dispatchEvent(new Event('pointerdown', { bubbles: true }))
      },
      expected: { interrupt: 'blank-click', reason: 'cancelled' }
    },
    {
      name: 'route switch',
      fire: () => {
        // 不真的改 location.hash：jsdom 的导航会额外排一个 task，污染 timer 计数。
        window.dispatchEvent(new HashChangeEvent('hashchange'))
      },
      expected: { interrupt: 'route', reason: 'interrupted' }
    },
    {
      name: 'mode switch',
      fire: () => layer.interrupt('mode'),
      expected: { interrupt: 'mode', reason: 'interrupted' }
    },
    {
      name: 'agent cancel',
      fire: () => {
        layer.clear()
      },
      expected: { interrupt: 'agent', reason: 'cancelled' }
    },
    {
      name: 'component unmount',
      fire: () => layer.dispose(),
      expected: { interrupt: 'unmount', reason: 'interrupted' }
    }
  ]

  it.each(cases)('$name ends the step and leaves nothing behind', ({ fire, expected }) => {
    const canvas = mountAnchor('design.canvas')
    layer.show({ target_anchor: 'design.canvas', text: '引导中' })
    expect(layer.stats()).toEqual({ listeners: 3, marked_targets: 1, overlays: 1, timers: 0 })

    fire()

    expect(layer.getState()).toMatchObject({
      status: 'idle',
      last_end: { guidance_id: 'gugu-test-1', ...expected }
    })
    expectZeroResidue(canvas)
  })

  it('unmount is idempotent and safe when idle', () => {
    layer.dispose()
    layer.dispose()
    expect(layer.getState().status).toBe('idle')
    expectZeroResidue()
  })
})

describe('timers', () => {
  it('auto-completes after the step timeout and clears the timer', () => {
    const canvas = mountAnchor('design.canvas')
    layer.show({ target_anchor: 'design.canvas', timeoutMs: 3000 })
    expect(layer.stats().timers).toBe(1)
    expect(vi.getTimerCount()).toBe(1)

    vi.advanceTimersByTime(3000)

    expect(layer.getState().last_end).toMatchObject({ interrupt: 'timeout', reason: 'done' })
    expectZeroResidue(canvas)
  })

  it('clears a pending timer when the user interrupts first', () => {
    mountAnchor('design.canvas')
    layer.show({ target_anchor: 'design.canvas', timeoutMs: 10_000 })
    layer.interrupt('mode')
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('listeners', () => {
  it('removes every listener it added', () => {
    mountAnchor('design.canvas')
    const docAdd = vi.spyOn(document, 'addEventListener')
    const docRemove = vi.spyOn(document, 'removeEventListener')
    const winAdd = vi.spyOn(window, 'addEventListener')
    const winRemove = vi.spyOn(window, 'removeEventListener')

    layer.show({ target_anchor: 'design.canvas' })
    // 2 个 document 监听（ESC + 空白点击）+ 1 个 window 监听（路由切换）。
    expect(docAdd.mock.calls.length).toBe(2)
    expect(winAdd.mock.calls.length).toBe(1)

    layer.interrupt('agent')
    expect(docRemove.mock.calls.length).toBe(2)
    expect(winRemove.mock.calls.length).toBe(1)
    expect(layer.stats().listeners).toBe(0)

    // 再按 ESC 不应产生任何状态变化（监听确实解绑了）。
    const before = layer.getState().last_end
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(layer.getState().last_end).toBe(before)

    docAdd.mockRestore()
    docRemove.mockRestore()
    winAdd.mockRestore()
    winRemove.mockRestore()
  })
})

describe('viewport and layout', () => {
  it('never steals focus while active and restores it on programmatic end', () => {
    const before = document.createElement('input')
    const during = document.createElement('input')
    document.body.append(before, during)
    before.focus()
    mountAnchor('design.canvas')

    layer.show({ target_anchor: 'design.canvas' })
    expect(document.activeElement).toBe(before)

    during.focus()
    expect(document.activeElement).toBe(during)

    layer.interrupt('mode')
    expect(document.activeElement).toBe(before)
  })

  it('keeps focus where the user put it after ESC / blank click', () => {
    const before = document.createElement('input')
    const during = document.createElement('input')
    document.body.append(before, during)
    before.focus()
    mountAnchor('design.canvas')
    layer.show({ target_anchor: 'design.canvas' })

    during.focus()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(document.activeElement).toBe(during)
  })

  it('does not scroll the page', () => {
    mountAnchor('design.canvas')
    const scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    const scrollBy = vi.spyOn(window, 'scrollBy').mockImplementation(() => {})
    const before = { x: window.scrollX, y: window.scrollY }

    layer.show({ target_anchor: 'design.canvas' })
    expect(window.scrollX).toBe(before.x)
    expect(window.scrollY).toBe(before.y)

    layer.interrupt('route')
    expect(scrollTo).not.toHaveBeenCalled()
    expect(scrollBy).not.toHaveBeenCalled()

    scrollTo.mockRestore()
    scrollBy.mockRestore()
  })

  it('does not touch the target style or the surrounding DOM (no layout change)', () => {
    const workspace = document.createElement('div')
    const canvas = document.createElement('button')
    canvas.setAttribute('data-agent-id', 'design.canvas')
    workspace.appendChild(canvas)
    document.body.appendChild(workspace)
    const childCount = workspace.children.length
    const styleAttr = canvas.getAttribute('style')

    layer.show({ target_anchor: 'design.canvas', style: 'box', text: '框一下' })
    // 唯一的 DOM 变化是目标上的标记属性；没有子节点、没有 style、没有 overlay 插进工作区。
    expect(workspace.children.length).toBe(childCount)
    expect(workspace.querySelectorAll('[data-gugu-guide]')).toHaveLength(0)
    expect(canvas.children).toHaveLength(0)
    expect(canvas.getAttribute('style')).toBe(styleAttr)
    expect(canvas.getAttribute('data-gugu-guide-target')).toBe('gugu-test-1')

    layer.interrupt('agent')
    expect(workspace.children.length).toBe(childCount)
    expect(canvas.getAttribute('data-gugu-guide-target')).toBeNull()
    expect(document.body.contains(workspace)).toBe(true)
  })
})

describe('exception path', () => {
  it('cleans up when mounting the overlay throws', () => {
    const broken = freshLayer({
      mount: () => {
        throw new Error('mount exploded')
      }
    })
    const canvas = mountAnchor('design.canvas')

    expect(errorCode(() => broken.show({ target_anchor: 'design.canvas' }))).toBe('render_failed')
    expect(broken.getState()).toMatchObject({
      status: 'idle',
      last_end: { interrupt: 'error', reason: 'interrupted' }
    })
    expect(document.querySelectorAll('[data-gugu-guide]')).toHaveLength(0)
    expect(document.querySelectorAll('[data-gugu-guide-target]')).toHaveLength(0)
    expect(broken.stats()).toEqual({ listeners: 0, marked_targets: 0, overlays: 0, timers: 0 })
    expect(canvas.getAttribute('style')).toBeNull()
    broken.dispose()
  })

  it('cleans up when measuring the target throws after the overlay mounted', () => {
    const canvas = mountAnchor('design.canvas')
    canvas.getBoundingClientRect = () => {
      throw new Error('measure exploded')
    }

    expect(errorCode(() => layer.show({ target_anchor: 'design.canvas' }))).toBe('render_failed')
    expectZeroResidue(canvas)
  })
})

describe('subscriptions', () => {
  it('emits active and idle transitions', () => {
    mountAnchor('design.canvas')
    const seen: string[] = []
    const unsubscribe = layer.subscribe(state => seen.push(state.status))
    layer.show({ target_anchor: 'design.canvas' })
    layer.interrupt('mode')
    unsubscribe()
    layer.show({ target_anchor: 'design.canvas' })

    expect(seen).toEqual(['active', 'idle'])
  })
})
