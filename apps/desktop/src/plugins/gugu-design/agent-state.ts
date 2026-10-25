/**
 * Agent 视角状态：surface / project / selection 的纯构造函数。
 *
 * 画布文档仍以 Host 的 project.json 为唯一权威；这里只发布"用户此刻在看
 * 什么、选了什么"，由 gugu-design 后端写到 `designs/agent-state.json`，供
 * capability `read_state` 读取。状态是缓存，不是文档：最后写入者胜。
 */
import { routeFromHash } from './agent-entry'

export type AgentSurface = 'design' | 'other' | 'settings' | 'work'

export interface AgentState {
  project_id: null | string
  route: string
  selection: string[]
  surface: AgentSurface
  updated_at: number
}

export function surfaceForRoute(route: string): AgentSurface {
  const path = (route.split('?')[0] ?? '') || '/'

  if (path === '/design' || path.startsWith('/design/')) {
    return 'design'
  }
  if (path === '/' || path === '/chat' || path.startsWith('/chat/')) {
    return 'work'
  }
  if (path === '/settings' || path.startsWith('/settings/')) {
    return 'settings'
  }
  return 'other'
}

export function buildAgentState(input: {
  hash: string
  pid: null | string
  selection: readonly string[]
  /** Seconds; injectable so tests do not depend on wall clock. */
  now?: number
}): AgentState {
  const route = routeFromHash(input.hash)

  return {
    project_id: input.pid ?? null,
    route,
    selection: [...input.selection],
    surface: surfaceForRoute(route),
    updated_at: input.now ?? Date.now() / 1000
  }
}
