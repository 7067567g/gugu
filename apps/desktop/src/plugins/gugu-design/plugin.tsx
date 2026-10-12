/**
 * 咕咕设计 —— gugu 三模式之一的 Design 入口。
 * 纯 SDK-consumer 插件（学 kanban 形态）：/design 整页 + 侧栏行 + ⌘K；
 * 后端为 plugins/gugu-design/dashboard/plugin_api.py（/api/plugins/gugu-design）。
 * 与 kanban 不同：这是产品核心模式，defaultEnabled: true。
 */
import type {
  HermesPlugin,
  PaletteContribution,
  RouteContribution,
  SidebarNavContribution
} from '@hermes/plugin-sdk'
import {
  host,
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA
} from '@hermes/plugin-sdk'

import { bindApi } from './api'
import { bindController } from './controller'
import { DesignPage } from './page'

const plugin: HermesPlugin = {
  id: 'gugu-design',
  name: '咕咕设计',
  description: '无限画布 AI 创作工作台：文生图/图生图、文/图生视频任务、素材落画布、本地工程持久化。',
  defaultEnabled: true,
  register(ctx) {
    bindApi(ctx.rest)
    bindController(ctx.rest)

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
      }
    ])
  }
}

export default plugin
