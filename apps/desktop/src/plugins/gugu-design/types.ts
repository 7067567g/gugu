/** gugu-design 文档模型（与 plugins/gugu-design/dashboard/plugin_api.py 的 CanvasDocument 对齐）。 */

export interface CanvasViewport {
  scale: number
  x: number
  y: number
}

export interface DesignAsset {
  bytes: number
  createdAt: number
  height: number
  id: string
  kind: 'image' | 'video'
  mediaType: string
  name: string
  versionId?: string
  width: number
}

/** 画布上的一个呈现节点（指向一个 asset/version）。 */
export interface CanvasElement {
  assetId: string
  height: number
  hidden: boolean
  id: string
  locked: boolean
  rotation: number
  type: 'image' | 'video'
  versionId: string
  width: number
  x: number
  y: number
  z: number
}

export interface DesignVersion {
  assetId: string
  createdAt: number
  id: string
  jobId?: string
  label: string
  parentVersionId?: string
}

export interface DesignJob {
  createdAt: number
  error?: { message: string; retryable: boolean }
  id: string
  kind: 'image' | 'video'
  outputAssetIds: string[]
  progress?: number | string
  request: Record<string, unknown>
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  updatedAt: number
  upstreamTaskId?: string
}

export interface CanvasDocument {
  assets: Record<string, DesignAsset>
  createdAt: number
  elements: Record<string, CanvasElement>
  id: string
  jobs: Record<string, DesignJob>
  name: string
  revision: number
  schemaVersion: 1
  updatedAt: number
  viewport: CanvasViewport
  versions: Record<string, DesignVersion>
}

export interface ProjectSummary {
  assets: number
  id: string
  name: string
  updatedAt: number
}
