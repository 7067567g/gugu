/**
 * 模式注册表 —— 三模式（Work/Design/IDE）的声明式定义与切换副作用。
 *
 * 架构文档：仓库根 `docs/02-三模式架构.md`。要点：
 * - 切换 = 写 `$appMode` + 导航到模式路由 + 应用模式布局预设（预设树在
 *   src/app/contrib/layout-presets.ts 注册，这里按 id 应用）。
 * - Work 不套预设：用户的现行布局就是 Work 布局。
 * - 每模式独立布局持久化（layout-persistence keyFor 泛化）推迟到 M2 Design
 *   布局定型后再做——见 docs/02 §4.3。
 * - IDE 路由暂指向 '/'（复用 chat+files 布局），M5 落地编辑器页后改指 '/ide'。
 */
import { NEW_CHAT_ROUTE } from '@/app/routes'
import { $appMode, type AppMode } from '@/store/app-mode'
import { applyDesktopLayoutPreset } from '@/store/pane-focus'

export interface ModeDef {
  icon: 'code' | 'comment-discussion' | 'layout'
  id: AppMode
  /** i18n key under shell.* */
  labelKey: 'modeDesign' | 'modeIde' | 'modeWork'
  /** 模式主页路由（hash 路由） */
  route: string
  /** 进入模式时应用的布局预设 id（null = 保持现状布局） */
  presetId: null | string
}

export const APP_MODE_DEFS: readonly ModeDef[] = [
  { id: 'work', route: NEW_CHAT_ROUTE, presetId: null, icon: 'comment-discussion', labelKey: 'modeWork' },
  { id: 'design', route: '/design', presetId: 'gugu.layout.design', icon: 'layout', labelKey: 'modeDesign' },
  { id: 'ide', route: NEW_CHAT_ROUTE, presetId: 'gugu.layout.ide', icon: 'code', labelKey: 'modeIde' }
]

export function modeDef(mode: AppMode): ModeDef {
  return APP_MODE_DEFS.find(m => m.id === mode) ?? APP_MODE_DEFS[0]
}

export function setAppMode(mode: AppMode): void {
  $appMode.set(mode)

  const def = modeDef(mode)

  window.location.hash = `#${def.route}`

  if (def.presetId) {
    applyDesktopLayoutPreset(def.presetId)
  }
}
