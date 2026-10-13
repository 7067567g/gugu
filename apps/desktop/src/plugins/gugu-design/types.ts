/** gugu-design 文档模型（与 plugins/gugu-design/dashboard/plugin_api.py 的 CanvasDocument 对齐）。
 *  注释锚点/版本图类型移植自 dsh-design-mode packages/design/design-mode/src/types.ts（MIT）。 */

export interface CanvasViewport {
  scale: number
  x: number
  y: number
}

/** 画布工具状态机（select/hand 常驻；注释工具是"下一次点击"的临时态）。 */
export type CanvasTool = 'hand' | 'point-comment' | 'rect-comment' | 'select'

/**
 * 注释锚点：坐标是**相对元素的归一化值**（0~1），不是画布绝对坐标——
 * 元素被移动/缩放后锚点仍然指在原处，这是 dsh 实现里最值得抄的一条不变量。
 */
export type CommentAnchor =
  | { kind: 'point'; x: number; y: number }
  | { kind: 'rect'; height: number; width: number; x: number; y: number }

/** 注释生命周期（draft→open 的送会话流程在 M4 接入）。 */
export type CommentStatus = 'draft' | 'open' | 'running' | 'resolved' | 'failed'

export interface CanvasComment {
  anchor: CommentAnchor
  createdAt: number
  id: string
  /** 锚点宿主版本（注释"钉"在哪张图上）。 */
  parentVersionId: string
  status: CommentStatus
  thread: Array<{ author: 'agent' | 'system' | 'user'; createdAt: number; id: string; text: string }>
  updatedAt: number
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
  comments: Record<string, CanvasComment>
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
