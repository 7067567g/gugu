/**
 * M3b 全局 runtime —— app 级导航 + 引导消费循环。
 *
 * 为什么在插件 `register()` 里启动，而不是页面里：导航必须能落到任意界面，
 * 引导也必须在任意界面可消费。插件的 register 与应用同生命周期，overlay 本来
 * 就挂 `document.body`；不需要给 SDK 新增 shell 扩展点（waist 保持窄）。
 *
 * 两条独立的轮询：
 * - guidance：约 1.2s；邮箱空了就本地收尾，ESC/点击/路由/模式/取消/卸载/异常
 *   全部由 `guidance.ts` 的 manager 负责零残留。
 * - navigation：约 250ms；读到 request 后真调 `host.navigate()`，随后发布
 *   agent-state 并带上 `nav_request_id`，Python executor 只认这条 ack。
 *
 * `pendingNav` 会在导航后保留几秒，页面自己的 hashchange / selection 发布也会
 * 带上同一个 id，避免防抖发布把 ack 覆盖掉。所有依赖可注入，测试直接驱动 tick。
 */
import type { AgentState } from './agent-state'
import { buildAgentState } from './agent-state'
import {
  acknowledgeNavigation,
  dismissGuidance,
  fetchGuidance,
  fetchNavigation,
  publishAgentState,
  type GuidanceCommand,
  type NavigationCommand,
  type NavigationScreen
} from './api'
import { $designView } from './controller'
import { guidanceLayer, planGuidanceTick, type GuidanceLayer } from './guidance'

export const NAVIGATION_ROUTES: Record<NavigationScreen, string> = {
  artifacts: '/artifacts',
  design: '/design',
  settings: '/settings',
  work: '/'
}

const GUIDANCE_POLL_MS = 1200
const NAVIGATION_POLL_MS = 250
/** Keep the nav id on subsequent publishes long enough for the ack round-trip. */
const NAV_ACK_HOLD_MS = 5000
const PUBLISH_DEBOUNCE_MS = 200

export interface GlobalRuntimeDeps {
  acknowledgeNavigation?: (requestId: string) => Promise<void> | void
  dismissGuidance?: (guidanceId: string) => Promise<void> | void
  fetchGuidance?: () => Promise<GuidanceCommand | null>
  fetchNavigation?: () => Promise<NavigationCommand | null>
  guidanceIntervalMs?: number
  hash?: () => string
  layer?: GuidanceLayer
  navigate?: (path: string) => void
  navigationIntervalMs?: number
  now?: () => number
  project?: () => { pid: null | string; selection: readonly string[] }
  publishAgentState?: (state: AgentState) => Promise<void> | void
  subscribeView?: (listener: () => void) => () => void
  window?: Window
}

export interface GlobalRuntime {
  stop: () => void
  tickGuidance: () => Promise<void>
  tickNavigation: () => Promise<void>
}

function defaultProject(): { pid: null | string; selection: readonly string[] } {
  const view = $designView.get()

  return { pid: view.pid, selection: view.selectedIds }
}

export function startGlobalRuntime(deps: GlobalRuntimeDeps = {}): GlobalRuntime {
  const win = deps.window ?? (typeof window === 'undefined' ? null : window)

  if (!win) {
    return { stop: () => {}, tickGuidance: async () => {}, tickNavigation: async () => {} }
  }

  const layer = deps.layer ?? guidanceLayer
  const navigate = deps.navigate ?? (path => { win.location.hash = `#${path}` })
  const hash = deps.hash ?? (() => win.location.hash)
  const now = deps.now ?? (() => Date.now() / 1000)
  const project = deps.project ?? defaultProject
  const readGuidance = deps.fetchGuidance ?? fetchGuidance
  const readNavigation = deps.fetchNavigation ?? fetchNavigation
  const publish = deps.publishAgentState ?? publishAgentState
  const clearGuidanceMailbox = deps.dismissGuidance ?? dismissGuidance
  const clearNavigationMailbox = deps.acknowledgeNavigation ?? acknowledgeNavigation
  const subscribeView = deps.subscribeView ?? ($designView.subscribe as (listener: () => void) => () => void)

  let stopped = false
  let guidanceInFlight = false
  let navigationInFlight = false
  let lastNavigationId: null | string = null
  let pendingNavigationId: null | string = null
  let pendingNavigationTimer: ReturnType<typeof setTimeout> | null = null
  let publishTimer: ReturnType<typeof setTimeout> | null = null
  const dismissed = new Set<string>()

  const publishCurrent = async (navRequestId: null | string): Promise<void> => {
    const current = project()

    await publish(
      buildAgentState({
        hash: hash(),
        navRequestId,
        now: now(),
        pid: current.pid,
        selection: current.selection
      })
    )
  }

  const schedulePublish = (): void => {
    if (publishTimer !== null) {
      clearTimeout(publishTimer)
    }

    publishTimer = setTimeout(() => {
      publishTimer = null
      if (!stopped) {
        void publishCurrent(pendingNavigationId)
      }
    }, PUBLISH_DEBOUNCE_MS)
  }

  // A local end (ESC / click / mode / timeout / agent cancel) deletes the
  // mailbox entry so the next poll does not paint a dismissed step again.
  const unsubscribeState = layer.subscribe(state => {
    if (state.status !== 'idle' || !state.last_end) {
      return
    }

    dismissed.add(state.last_end.guidance_id)
    if (dismissed.size > 32) {
      dismissed.clear()
    }
    void clearGuidanceMailbox(state.last_end.guidance_id)
  })

  const tickGuidance = async (): Promise<void> => {
    if (stopped || guidanceInFlight) {
      return
    }

    guidanceInFlight = true

    try {
      const command = await readGuidance()

      if (stopped) {
        return
      }

      const action = planGuidanceTick({ active_id: layer.activeId(), command, dismissed })

      if (action === 'ignore') {
        return
      }
      if (action === 'clear') {
        layer.clear()
        return
      }
      if (!command) {
        return
      }
      if (layer.activeId()) {
        // Mailbox moved to another step: replace, never stack.
        layer.clear()
      }
      try {
        layer.show({
          id: command.guidance_id,
          style: command.style,
          target_anchor: command.target_anchor,
          text: command.text ?? '',
          timeoutMs: command.timeout_ms ?? 0
        })
      } catch {
        // The anchor is not on this surface right now. Do not leave a fake
        // active state; dismiss this one step and let the agent decide.
        dismissed.add(command.guidance_id)
        void clearGuidanceMailbox(command.guidance_id)
      }
    } catch {
      // Backend not ready / remote route without the plugin API: retry later.
    } finally {
      guidanceInFlight = false
    }
  }

  const tickNavigation = async (): Promise<void> => {
    if (stopped || navigationInFlight) {
      return
    }

    navigationInFlight = true

    try {
      const command = await readNavigation()

      if (stopped || !command) {
        return
      }
      if (command.request_id === lastNavigationId) {
        return
      }

      const route = NAVIGATION_ROUTES[command.screen]
      const expiresAt = Number(command.expires_at ?? 0)
      const expired = expiresAt > 0 && expiresAt < now()

      if (!route || expired) {
        lastNavigationId = command.request_id
        void clearNavigationMailbox(command.request_id)
        return
      }

      navigate(route)
      lastNavigationId = command.request_id
      pendingNavigationId = command.request_id
      if (pendingNavigationTimer !== null) {
        clearTimeout(pendingNavigationTimer)
      }
      pendingNavigationTimer = setTimeout(() => {
        pendingNavigationId = null
        pendingNavigationTimer = null
      }, NAV_ACK_HOLD_MS)
      await publishCurrent(command.request_id)
      void clearNavigationMailbox(command.request_id)
    } catch {
      // A malformed request must not break the app or the guidance loop.
    } finally {
      navigationInFlight = false
    }
  }

  const unsubscribeView = subscribeView(schedulePublish)
  win.addEventListener('hashchange', schedulePublish)
  void publishCurrent(null)

  const guidanceTimer = win.setInterval(() => void tickGuidance(), deps.guidanceIntervalMs ?? GUIDANCE_POLL_MS)
  const navigationTimer = win.setInterval(
    () => void tickNavigation(),
    deps.navigationIntervalMs ?? NAVIGATION_POLL_MS
  )
  void tickGuidance()
  void tickNavigation()

  return {
    stop: () => {
      if (stopped) {
        return
      }
      stopped = true
      win.clearInterval(guidanceTimer)
      win.clearInterval(navigationTimer)
      if (publishTimer !== null) {
        clearTimeout(publishTimer)
        publishTimer = null
      }
      if (pendingNavigationTimer !== null) {
        clearTimeout(pendingNavigationTimer)
        pendingNavigationTimer = null
      }
      win.removeEventListener('hashchange', schedulePublish)
      unsubscribeView()
      // dispose() emits an idle end first, so the subscriber above still gets
      // its one chance to clear the mailbox; then all resources are gone.
      layer.dispose()
      unsubscribeState()
    },
    tickGuidance,
    tickNavigation
  }
}

/** Test hook / diagnostics: the fixed screen→route table is data, not UI. */
export function routeForScreen(screen: NavigationScreen): string {
  return NAVIGATION_ROUTES[screen]
}
