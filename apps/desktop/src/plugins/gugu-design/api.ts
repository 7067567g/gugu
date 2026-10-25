/** gugu-design 数据层：ctx.rest 命名空间门 + React Query。 */
import { useMutation, useQuery, useQueryClient } from '@hermes/plugin-sdk'

import type { AgentState } from './agent-state'
import { designController } from './controller'
import type { CanvasDocument, DesignAsset, DesignJob, ProjectSummary } from './types'

type Rest = <T>(path: string, opts?: {
  body?: unknown
  method?: string
  timeoutMs?: number
  upload?: { bytes: ArrayBuffer; contentType?: string; filename: string }
}) => Promise<T>

let rest: null | Rest = null

export function bindApi(r: Rest): void {
  rest = r
}

function need<T>(path: string, opts?: {
  body?: unknown
  method?: string
  timeoutMs?: number
  upload?: { bytes: ArrayBuffer; contentType?: string; filename: string }
}): Promise<T> {
  if (!rest) {
    throw new Error('gugu-design api not bound')
  }

  return rest<T>(path, opts)
}

/** 拉取 asset 二进制 → objectURL（带缓存）。走插件 rest 门的 base64 通道：
 *  桌面 bridge 的 `/api/*` 带 session token，`<img>` 直连会被 dashboard 中间件拦，
 *  因此 asset 统一 `?b64=1` 拿 JSON 再转 blob。 */
const urlCache = new Map<string, Promise<string>>()

export function fetchAssetUrl(pid: string, assetId: string): Promise<string> {
  const key = pid + ':' + assetId
  const hit = urlCache.get(key)

  if (hit) {
    return hit
  }

  const pending = (async () => {
    const data = await need<{ data: string; mediaType: string }>(
      `/projects/${pid}/assets/${assetId}?b64=1`,
      { timeoutMs: 120_000 }
    )
    const bytes = Uint8Array.from(atob(data.data), c => c.charCodeAt(0))
    const blob = new Blob([bytes], { type: data.mediaType })

    return URL.createObjectURL(blob)
  })()

  urlCache.set(key, pending)

  return pending
}

// ---- 查询 hooks ------------------------------------------------------------

export function useProjects() {
  return useQuery({
    queryFn: () => need<{ projects: ProjectSummary[] }>('/projects'),
    queryKey: ['gugu-design', 'projects']
  })
}

export function useJobs(pid: null | string) {
  return useQuery({
    enabled: Boolean(pid),
    queryFn: () => need<{ jobs: DesignJob[] }>(`/projects/${pid}/jobs`),
    queryKey: ['gugu-design', 'jobs', pid],
    refetchInterval: 2000
  })
}

export function useGatewayStatus() {
  return useQuery({
    queryFn: () => need<{ base_url?: string; configured: boolean }>('/gateway'),
    queryKey: ['gugu-design', 'gateway']
  })
}

/** 网关按模态收口的模型清单（生成面板下拉）。 */
export function useGatewayModels(modality: 'image' | 'video') {
  return useQuery({
    queryFn: () => need<{ models: Array<{ id: string; label: string }> }>(`/gateway-models?modality=${modality}`),
    queryKey: ['gugu-design', 'gateway-models', modality],
    staleTime: 60_000
  })
}

// ---- mutations --------------------------------------------------------------

export function useCreateProject(onCreated: (pid: string) => void) {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: (name: string) => need<{ id: string }>('/projects', { body: { name }, method: 'POST' }),
    onSuccess: res => {
      void qc.invalidateQueries({ queryKey: ['gugu-design', 'projects'] })
      onCreated(res.id)
    }
  })
}

export function useDeleteProject() {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: (pid: string) => need(`/projects/${pid}`, { method: 'DELETE' }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['gugu-design', 'projects'] })
  })
}

export function useSubmitJob(pid: null | string) {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: (body: { kind: 'image' | 'video'; request: Record<string, unknown> }) =>
      need<{ jobId: string }>(`/projects/${pid}/jobs`, { body, method: 'POST' }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['gugu-design', 'jobs', pid] })
  })
}

export async function uploadAsset(pid: string, file: File): Promise<DesignAsset> {
  const buf = await file.arrayBuffer()

  const res = await need<{ asset: DesignAsset }>(`/projects/${pid}/assets`, {
    method: 'POST',
    upload: { bytes: buf, contentType: file.type || 'image/png', filename: file.name }
  })

  return res.asset
}

/** job 轮询成功后：拉 asset 元数据并落画布（由 page 的 effect 驱动）。 */
export async function ingestCompletedJobs(pid: string, jobs: DesignJob[], known: CanvasDocument | null): Promise<void> {
  for (const job of jobs) {
    if (job.status !== 'succeeded' || job.outputAssetIds.length === 0) {
      continue
    }

    const fresh = job.outputAssetIds.filter(aid => !known?.assets[aid])

    if (fresh.length === 0 && known?.jobs[job.id]) {
      continue
    }

    const { document } = await need<{ document: CanvasDocument }>(`/projects/${pid}`)
    designController.ingestJob(document.jobs[job.id] ?? job, document.assets, true)
  }
}

/** Best-effort surface/selection publication for capability read_state.
 *  The canvas document stays Host-authoritative; this is a cache the agent
 *  reads, so a failed write must never block or toast the canvas. */
export async function publishAgentState(state: AgentState): Promise<void> {
  try {
    await need('/agent-state', { body: state, method: 'PUT', timeoutMs: 4000 })
  } catch {
    // Backend not ready / remote route without the plugin API: state is stale,
    // not fatal. The next successful publish replaces it.
  }
}

export interface CanvasExport {
  bytes: number
  data: string
  filename: string
  revision: number
  sha256: string
}

/** M1 export: the backend builds one self-contained zip; the renderer just hands
 *  it to the user's download flow. The rest门 returns JSON, so the zip travels
 *  base64 — same channel as asset fetches. */
export async function exportCanvas(pid: string): Promise<CanvasExport> {
  return need<CanvasExport>(`/projects/${pid}/export`, { method: 'POST', timeoutMs: 120_000 })
}

export function downloadCanvasExport(result: CanvasExport): void {
  const bytes = Uint8Array.from(atob(result.data), char => char.charCodeAt(0))
  const blob = new Blob([bytes], { type: 'application/zip' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')

  anchor.download = result.filename
  anchor.href = url
  anchor.click()
  URL.revokeObjectURL(url)
}

export interface GuidanceCommand {
  created_at?: number
  guidance_id: string
  style?: 'box' | 'highlight' | 'label'
  target_anchor: string
  text?: string
  timeout_ms?: number
}

/** M2: read the one active guidance step (mailbox; polling stands in for the
 *  renderer-directed host channel that M1 deferred). */
export async function fetchGuidance(): Promise<GuidanceCommand | null> {
  const result = await need<{ guidance: GuidanceCommand | null }>('/guidance', { timeoutMs: 4000 })

  return result.guidance ?? null
}

/** Best-effort mailbox cleanup after a local interrupt. Scoped to one id so a
 *  late dismiss cannot delete the agent's next step. */
export async function dismissGuidance(guidanceId: string): Promise<void> {
  try {
    await need(`/guidance?guidance_id=${encodeURIComponent(guidanceId)}`, { method: 'DELETE', timeoutMs: 4000 })
  } catch {
    // Backend not ready: a stale step will be re-rendered on the next poll and
    // re-dismissed; local DOM is already clean, so this is not fatal.
  }
}

export type NavigationScreen = 'artifacts' | 'design' | 'settings' | 'work'

export interface NavigationCommand {
  created_at?: number
  expires_at?: number
  request_id: string
  route: string
  screen: NavigationScreen
}

/** M3b: read the one pending navigation request written by `navigate`. */
export async function fetchNavigation(): Promise<NavigationCommand | null> {
  const result = await need<{ navigation: NavigationCommand | null }>('/navigation', { timeoutMs: 4000 })

  return result.navigation ?? null
}

/** One-shot cleanup after the router moved. Scoped to one request id so a late
 *  delete cannot erase the agent's next navigation. */
export async function acknowledgeNavigation(requestId: string): Promise<void> {
  try {
    await need(`/navigation?request_id=${encodeURIComponent(requestId)}`, { method: 'DELETE', timeoutMs: 4000 })
  } catch {
    // The executor also clears its own mailbox; a failed DELETE only leaves a
    // stale request that expires. Not fatal to the UI.
  }
}
