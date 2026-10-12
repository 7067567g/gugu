/**
 * 应用模式（Work / Design / IDE）—— gugu 三模式的顶层状态。
 *
 * 模式是 **UI 概念**，不是后端隔离概念：一个壳、一个后端进程、三棵 UI 树。
 * 决策与扩展点清单见仓库根 `docs/02-三模式架构.md`（ADR-1 解释了为什么不用
 * 三 profile 承载）。切换副作用（路由 + 布局预设）在 `src/app/modes.ts`，
 * 本模块只拥有持久化状态本身。
 */
import { type Codec, persistentAtom } from '@/lib/persisted'

export type AppMode = 'design' | 'ide' | 'work'

export const APP_MODES: readonly AppMode[] = ['work', 'design', 'ide']

export const DEFAULT_APP_MODE: AppMode = 'work'

const APP_MODE_STORAGE_KEY = 'gugu.appMode.v1'

const appModeCodec: Codec<AppMode> = {
  decode: raw => (APP_MODES.includes(raw as AppMode) ? (raw as AppMode) : DEFAULT_APP_MODE),
  encode: mode => (mode === DEFAULT_APP_MODE ? null : mode)
}

/** 当前应用模式。Advanced/simple（InterfaceMode）与本维度正交：界面繁简 × 工作种类。 */
export const $appMode = persistentAtom<AppMode>(APP_MODE_STORAGE_KEY, DEFAULT_APP_MODE, appModeCodec)
