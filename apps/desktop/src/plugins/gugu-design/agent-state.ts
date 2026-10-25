/**
 * Agent 视角状态：surface / project / selection 的纯构造函数。
 *
 * 画布文档仍以 Host 的 project.json 为唯一权威；这里只发布"用户此刻在看
 * 什么、选了什么"，由 gugu-design 后端写到 `designs/agent-state.json`，供
 * capability `read_state` 读取。状态是缓存，不是文档：最后写入者胜。
 */
import { routeFromHash } from './agent-entry'

export type AgentSurface = 'artifacts' | 'design' | 'other' | 'settings' | 'work'

export interface AgentState {
  /** One-shot navigate request this snapshot acknowledges (M3b). */
  nav_request_id: null | string
  project_id: null | string
  route: string
  /** Screen the agent asked for; equal to `surface` once the route lands. */
  screen: AgentSurface
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
  if (path === '/artifacts' || path.startsWith('/artifacts/')) {
    return 'artifacts'
  }
  return 'other'
}

export function buildAgentState(input: {
  hash: string
  navRequestId?: null | string
  pid: null | string
  selection: readonly string[]
  /** Seconds; injectable so tests do not depend on wall clock. */
  now?: number
}): AgentState {
  const route = routeFromHash(input.hash)
  const surface = surfaceForRoute(route)

  return {
    nav_request_id: input.navRequestId ?? null,
    project_id: input.pid ?? null,
    route,
    screen: surface,
    selection: [...input.selection],
    surface,
    updated_at: input.now ?? Date.now() / 1000
  }
}
