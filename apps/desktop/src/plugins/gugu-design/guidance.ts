/**
 * M2 引导层（guidance）—— 高亮/图框/说明 + 零残留生命周期。
 *
 * 产品契约（strategy/product/gugu-p0-consolidation.md §4）：
 *   idle → active(step) → done | cancelled | interrupted → idle
 * 中断源必须全覆盖：ESC / 点击空白 / 路由切换 / 模式切换 / Agent 取消 /
 * 组件卸载 / 异常。任何一次结束都必须做到：
 *   overlay DOM=0、[data-gugu-guide-target]=0、listener=0、timer=0、
 *   不改变焦点与滚动、不写目标 style（因此不影响布局）。
 *
 * 设计取舍：
 * - overlay 只挂在 document.body，position:fixed + pointer-events:none；不改目标
 *   元素样式，也不把节点插进目标子树。目标只加一个布尔标记属性。
 * - 锚点解析用 `[data-agent-id]` 精确比对（不用 CSS.escape，避免注入/兼容性）。
 * - 引导状态不进入 React：画布不因引导重渲染；页面只负责喂命令与卸载清理。
 * - 程序性结束（mode/route/agent/unmount/error/timeout）恢复引导开始前的焦点与
 *   滚动；用户手势结束（ESC/空白点击）不抢回焦点——用户点哪里焦点就在哪里。
 */

export type GuidanceEndReason = 'cancelled' | 'done' | 'interrupted'

export type GuidanceInterrupt =
  | 'agent'
  | 'blank-click'
  | 'error'
  | 'escape'
  | 'mode'
  | 'route'
  | 'timeout'
  | 'unmount'

export type GuidanceStyle = 'box' | 'highlight' | 'label'

const OVERLAY_ATTR = 'data-gugu-guide'
const TARGET_ATTR = 'data-gugu-guide-target'
const ANCHOR_ATTR = 'data-agent-id'
const OVERLAY_ANCHOR = 'guidance.overlay'
const STYLES: readonly GuidanceStyle[] = ['box', 'highlight', 'label']
const USER_INTERRUPTS: readonly GuidanceInterrupt[] = ['blank-click', 'escape']

export interface GuidanceStep {
  /** 上游命令 id；缺省由 manager 生成。同一 id 不会被重复展示（页面去重）。 */
  id?: string
  style?: GuidanceStyle
  target_anchor: string
  text?: string
  /** >0 时到点自动结束（reason=done, interrupt=timeout）。 */
  timeoutMs?: number
}

export interface GuidanceEnd {
  guidance_id: string
  interrupt: GuidanceInterrupt
  reason: GuidanceEndReason
}

export interface GuidanceStepView {
  index: number
  style: GuidanceStyle
  target_anchor: string
  text: string
}

export interface GuidanceState {
  guidance_id: null | string
  last_end: GuidanceEnd | null
  status: 'active' | 'idle'
  step: GuidanceStepView | null
}

export interface GuidanceStats {
  listeners: number
  marked_targets: number
  overlays: number
  timers: number
}

export interface GuidanceLayerOptions {
  document?: Document
  /** 测试注入；默认 `gugu-guidance-<seq>-<now>`。 */
  idFactory?: () => string
  /** 测试注入异常路径；默认在 document.body 建 overlay 根。 */
  mount?: (doc: Document) => HTMLElement
  now?: () => number
  window?: Window
}

export class GuidanceError extends Error {
  readonly code: string

  constructor(code: string, message: string) {
    super(message)
    this.code = code
    this.name = 'GuidanceError'
  }
}

function defaultMount(doc: Document): HTMLElement {
  const root = doc.createElement('div')
  root.setAttribute(OVERLAY_ATTR, '')
  root.setAttribute(ANCHOR_ATTR, OVERLAY_ANCHOR)
  root.setAttribute('aria-hidden', 'true')
  root.setAttribute('role', 'presentation')
  // fixed + pointer-events:none：不参与文档流、不挡点击、不改变布局。
  root.style.cssText = 'position:fixed;inset:0;z-index:2147483000;pointer-events:none'
  doc.body.appendChild(root)
  return root
}

function resolveAnchor(doc: Document, anchor: string): HTMLElement | null {
  const nodes = doc.querySelectorAll<HTMLElement>(`[${ANCHOR_ATTR}]`)
  for (const node of nodes) {
    if (node.getAttribute(ANCHOR_ATTR) === anchor) {
      return node
    }
  }
  return null
}

function frameCss(style: GuidanceStyle): string {
  if (style === 'box') {
    return 'border:1.5px dashed #4f8cff;border-radius:8px;box-shadow:0 0 0 3px rgba(79,140,255,.12)'
  }
  return 'border:2px solid #4f8cff;border-radius:8px;box-shadow:0 0 0 3px rgba(79,140,255,.28)'
}

export interface GuidanceLayer {
  activeId: () => null | string
  /** Agent 取消 / clear_guidance：可重复调用，返回是否真的清掉了东西。 */
  clear: () => boolean
  /** 组件卸载 / 插件 dispose：断开所有订阅与资源。 */
  dispose: () => void
  getState: () => GuidanceState
  interrupt: (source: GuidanceInterrupt) => boolean
  show: (step: GuidanceStep) => { guidance_id: string }
  stats: () => GuidanceStats
  subscribe: (listener: (state: GuidanceState) => void) => () => void
}

export function createGuidanceLayer(options: GuidanceLayerOptions = {}): GuidanceLayer {
  const doc = options.document ?? (typeof document === 'undefined' ? null : document)
  const win = options.window ?? (typeof window === 'undefined' ? null : window)
  const now = options.now ?? (() => Date.now())
  let seq = 0
  const idFactory = options.idFactory ?? (() => `gugu-guidance-${++seq}-${Math.round(now() / 1000)}`)

  let state: GuidanceState = { guidance_id: null, last_end: null, status: 'idle', step: null }
  let root: HTMLElement | null = null
  let target: HTMLElement | null = null
  let marker: null | string = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let listeners = 0
  let savedFocus: HTMLElement | null = null
  let savedScroll = { x: 0, y: 0 }
  let cleanups: Array<() => void> = []
  const subscribers = new Set<(state: GuidanceState) => void>()

  function needDoc(): Document {
    if (!doc) {
      throw new GuidanceError('no_document', '引导层需要 DOM 环境')
    }
    return doc
  }

  function emit(): void {
    for (const listener of subscribers) {
      try {
        listener(state)
      } catch {
        // 订阅方是页面接线；它坏了不能阻断清理。
      }
    }
  }

  function listen(type: string, handler: (event: Event) => void): void {
    const host: EventTarget | null = type === 'hashchange' ? win : doc
    if (!host) {
      return
    }
    const wrapped = (event: Event) => handler(event)
    host.addEventListener(type, wrapped, true)
    listeners += 1
    cleanups.push(() => {
      host.removeEventListener(type, wrapped, true)
      listeners -= 1
    })
  }

  function teardownDom(): void {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    const pending = cleanups
    cleanups = []
    for (const undo of pending) {
      undo()
    }
    if (target && marker !== null && target.getAttribute(TARGET_ATTR) === marker) {
      target.removeAttribute(TARGET_ATTR)
    }
    target = null
    marker = null
    if (root && root.parentNode) {
      root.parentNode.removeChild(root)
    }
    root = null
  }

  function restoreViewport(): void {
    const focus = savedFocus
    savedFocus = null
    if (focus && focus.isConnected) {
      try {
        if (needDoc().activeElement !== focus) {
          focus.focus({ preventScroll: true })
        }
      } catch {
        // 目标已不可聚焦（例如被卸载/禁用）：焦点留给浏览器，绝不抛给调用方。
      }
    }
    if (win && (win.scrollX !== savedScroll.x || win.scrollY !== savedScroll.y)) {
      try {
        win.scrollTo(savedScroll.x, savedScroll.y)
      } catch {
        // 无滚动能力的宿主（jsdom/测试替身）忽略。
      }
    }
  }

  function end(reason: GuidanceEndReason, interrupt: GuidanceInterrupt): boolean {
    if (state.status !== 'active' || state.guidance_id === null) {
      return false
    }
    const finished = state.guidance_id
    teardownDom()
    if (!USER_INTERRUPTS.includes(interrupt)) {
      restoreViewport()
    } else {
      savedFocus = null
    }
    state = {
      guidance_id: null,
      last_end: { guidance_id: finished, interrupt, reason },
      status: 'idle',
      step: null
    }
    emit()
    return true
  }

  function render(host: HTMLElement, at: HTMLElement, style: GuidanceStyle, text: string): void {
    const view = win
    const rect = at.getBoundingClientRect()
    const visible = style === 'label' ? text.length > 0 : true

    if (visible && style !== 'label') {
      const frame = needDoc().createElement('div')
      frame.setAttribute('data-gugu-guide-frame', style)
      frame.style.cssText =
        `position:absolute;left:${rect.left}px;top:${rect.top}px;` +
        `width:${rect.width}px;height:${rect.height}px;box-sizing:border-box;` +
        `pointer-events:none;${frameCss(style)}`
      host.appendChild(frame)
    }

    if (!text) {
      return
    }

    const label = needDoc().createElement('div')
    label.setAttribute('data-gugu-guide-label', '')
    label.textContent = text
    const height = view?.innerHeight ?? 0
    const below = rect.bottom + 8
    const top = height > 0 && below + 40 > height ? Math.max(0, rect.top - 40) : below
    label.style.cssText =
      `position:absolute;left:${Math.max(0, rect.left)}px;top:${top}px;` +
      'max-width:280px;padding:6px 10px;border-radius:8px;background:#111827;color:#fff;' +
      'font:12px/1.45 system-ui,sans-serif;white-space:pre-wrap;pointer-events:none;' +
      'box-shadow:0 4px 16px rgba(0,0,0,.28)'
    host.appendChild(label)
  }

  function show(step: GuidanceStep): { guidance_id: string } {
    const targetAnchor = String(step.target_anchor ?? '').trim()
    if (!targetAnchor) {
      throw new GuidanceError('invalid_arguments', 'target_anchor 不能为空')
    }
    const style = step.style ?? 'highlight'
    if (!STYLES.includes(style)) {
      throw new GuidanceError('invalid_arguments', `未知引导样式: ${String(style)}`)
    }
    const document = needDoc()
    const at = resolveAnchor(document, targetAnchor)
    if (!at) {
      throw new GuidanceError('anchor_not_found', `当前界面没有锚点: ${targetAnchor}`)
    }

    // 同一时刻只允许一个逐步引导；旧的一律按 Agent 替换收尾，残留先清零。
    if (state.status === 'active') {
      end('interrupted', 'agent')
    }

    const guidanceId = (step.id ?? '').trim() || idFactory()
    const text = String(step.text ?? '').slice(0, 400)
    savedFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    savedScroll = { x: win?.scrollX ?? 0, y: win?.scrollY ?? 0 }

    try {
      const mounted = (options.mount ?? defaultMount)(document)
      if (!mounted || !mounted.isConnected) {
        throw new GuidanceError('render_failed', '引导 overlay 未能挂载')
      }
      root = mounted
      render(mounted, at, style, text)
      at.setAttribute(TARGET_ATTR, guidanceId)
      target = at
      marker = guidanceId

      listen('keydown', event => {
        if ((event as KeyboardEvent).key === 'Escape') {
          end('cancelled', 'escape')
        }
      })
      listen('pointerdown', event => {
        const node = event.target as Node | null
        if (root && node && root.contains(node)) {
          return
        }
        end('cancelled', 'blank-click')
      })
      listen('hashchange', () => {
        end('interrupted', 'route')
      })

      if ((step.timeoutMs ?? 0) > 0) {
        timer = setTimeout(() => {
          timer = null
          end('done', 'timeout')
        }, step.timeoutMs)
      }

      state = {
        guidance_id: guidanceId,
        last_end: null,
        status: 'active',
        step: { index: 0, style, target_anchor: targetAnchor, text }
      }
      emit()
      return { guidance_id: guidanceId }
    } catch (error) {
      teardownDom()
      savedFocus = null
      state = {
        guidance_id: null,
        last_end: {
          guidance_id: guidanceId,
          interrupt: 'error',
          reason: 'interrupted'
        },
        status: 'idle',
        step: null
      }
      emit()
      if (error instanceof GuidanceError) {
        throw error
      }
      throw new GuidanceError('render_failed', error instanceof Error ? error.message : String(error))
    }
  }

  function interrupt(source: GuidanceInterrupt): boolean {
    if (source === 'escape' || source === 'blank-click') {
      return end('cancelled', source)
    }
    if (source === 'timeout') {
      return end('done', 'timeout')
    }
    return end('interrupted', source)
  }

  return {
    activeId: () => state.guidance_id,
    clear: () => end('cancelled', 'agent'),
    dispose: () => {
      end('interrupted', 'unmount')
      subscribers.clear()
    },
    getState: () => state,
    interrupt,
    show,
    stats: () => ({
      listeners,
      marked_targets: doc ? doc.querySelectorAll(`[${TARGET_ATTR}]`).length : 0,
      overlays: doc ? doc.querySelectorAll(`[${OVERLAY_ATTR}]`).length : 0,
      timers: timer === null ? 0 : 1
    }),
    subscribe: listener => {
      subscribers.add(listener)
      return () => {
        subscribers.delete(listener)
      }
    }
  }
}

/** 桌面单例：overlay 直接挂 body，不参与 React 渲染。 */
export const guidanceLayer = createGuidanceLayer()
