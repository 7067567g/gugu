/**
 * 全局 AI 入口的纯决策层（无 React / SDK 依赖，可直接单测）。
 *
 * 产品契约（P0 §1）：任意界面 ≤1 次点击或快捷键找到 AI 输入框；优先复用现有
 * composer，不新造聊天框。桌面只有两种真实输入框：
 * - `/design` 且已打开工程 → 现有 GeneratePanel 的 textarea（`design.composer`）；
 * - 其它情况 → 核心 Chat composer（`composerHost.focus()`）。
 *
 * `/design` 的工程列表页没有 chat composer，所以无工程时先回 `/` 再聚焦；
 * 这是计划里写明的窄兜底，不是新界面。
 */

export type AiEntryTarget = 'design-composer' | 'global-composer'

export interface AiEntryPlan {
  target: AiEntryTarget
  /** 需要先导航到哪里；null = 留在当前路由。 */
  navigateTo: null | string
}

/** Hash route → canonical path (`#/design?x=1` → `/design`, empty → `/`). */
export function routeFromHash(hash: string): string {
  const raw = (hash || '').replace(/^#/, '')
  const path = raw.split('?')[0] ?? ''
  if (!path) {
    return '/'
  }
  return path.startsWith('/') ? path : `/${path}`
}

export function planAiEntry(input: { hasDocument: boolean; hash: string }): AiEntryPlan {
  const route = routeFromHash(input.hash)
  const onDesign = route === '/design' || route.startsWith('/design/')

  if (onDesign && input.hasDocument) {
    return { target: 'design-composer', navigateTo: null }
  }
  if (onDesign) {
    return { target: 'global-composer', navigateTo: '/' }
  }
  return { target: 'global-composer', navigateTo: null }
}

export interface AiEntryDeps {
  hash: () => string
  hasDocument: () => boolean
  navigate: (path: string) => void
  /** Focus the mounted design composer; return false when it is not mounted. */
  focusDesignComposer: () => boolean
  focusGlobalComposer: () => void
}

/**
 * Execute the plan against injected host verbs. Returns the composer that was
 * actually focused, so a caller (or a test) can assert the fallback happened.
 */
export function runAiEntry(deps: AiEntryDeps): AiEntryTarget {
  const plan = planAiEntry({ hash: deps.hash(), hasDocument: deps.hasDocument() })

  if (plan.navigateTo) {
    deps.navigate(plan.navigateTo)
  }

  if (plan.target === 'design-composer' && deps.focusDesignComposer()) {
    return 'design-composer'
  }

  deps.focusGlobalComposer()
  return 'global-composer'
}
