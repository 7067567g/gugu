/**
 * 咕咕设计 —— gugu 三模式之一的 Design 入口。
 * 纯 SDK-consumer 插件（学 kanban 形态）：/design 整页 + 侧栏行 + ⌘K；
 * 后端为 plugins/gugu-design/dashboard/plugin_api.py（/api/plugins/gugu-design）。
 * 与 kanban 不同：这是产品核心模式，defaultEnabled: true。
 */
import type {
  HermesPlugin,
  KeybindContribution,
  PaletteContribution,
  RouteContribution,
  SidebarNavContribution
} from '@hermes/plugin-sdk'
import {
  host,
  KEYBINDS_AREA,
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA
} from '@hermes/plugin-sdk'

import { runAiEntry } from './agent-entry'
import { bindApi } from './api'
import { $designView, bindController } from './controller'
import { startGlobalRuntime } from './global-runtime'
import { t } from './i18n'
import { DesignPage } from './page'

/** Focus the EXISTING design composer (the GeneratePanel textarea), not a new chat box. */
function focusDesignComposer(): boolean {
  const field = document.querySelector<HTMLTextAreaElement>('[data-agent-id="design.composer"]')

  if (!field) {
    return false
  }

  field.focus()

  return true
}

/** One shortcut / one palette row reaches an AI input from any surface. */
function openAiEntry(): void {
  runAiEntry({
    hash: () => window.location.hash,
    hasDocument: () => Boolean($designView.get().document),
    navigate: path => host.navigate(path),
    focusDesignComposer,
    focusGlobalComposer: () => {
      // The chat surface only exists outside /design. If the design composer is
      // not mounted (gateway not configured / project switching), land on work.
      if (window.location.hash.startsWith('#/design')) {
        host.navigate('/')
      }
      // The chat surface mounts asynchronously after the fallback navigation.
      host.composer.focus()
      window.setTimeout(() => host.composer.focus(), 250)
    }
  })
}

const plugin: HermesPlugin = {
  id: 'gugu-design',
  name: '咕咕设计',
  description: '无限画布 AI 创作工作台：文生图/图生图、文/图生视频任务、素材落画布、本地工程持久化。',
  defaultEnabled: true,
  register(ctx) {
    bindApi(ctx.rest)
    bindController(ctx.rest)

    // App-level runtime (not the /design page): navigation must land on any
    // screen and guidance must be consumable everywhere. It also owns
    // agent-state publication so a navigation ack cannot be overwritten by a
    // separate debounced publisher.
    const runtime = startGlobalRuntime({ navigate: path => host.navigate(path) })
    ctx.onDispose(() => runtime.stop())

    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/design' } satisfies RouteContribution,
        render: () => <DesignPage />
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 40,
        data: { codicon: 'layout', label: '咕咕设计', path: '/design' } satisfies SidebarNavContribution
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'gugu-design.open',
          label: '打开咕咕设计',
          keywords: ['design', 'canvas', '画布', '设计'],
          run: () => host.navigate('/design')
        } satisfies PaletteContribution
      },
      {
        id: 'ai-entry',
        area: PALETTE_AREA,
        data: {
          id: 'gugu.aiEntry',
          action: 'gugu.aiEntry',
          label: t('aiEntry'),
          keywords: ['ai', 'ask', 'composer', 'prompt', '输入', '问'],
          run: openAiEntry
        } satisfies PaletteContribution
      },
      {
        id: 'ai-entry',
        area: KEYBINDS_AREA,
        data: {
          id: 'gugu.aiEntry',
          category: 'view',
          defaults: ['mod+alt+a'],
          label: t('aiEntry'),
          run: openAiEntry
        } satisfies KeybindContribution
      }
    ])
  }
}

export default plugin
