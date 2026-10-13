/**
 * gugu-design 会话控制器 —— 本地乐观文档状态 + Host 权威持久化。
 *
 * 架构移植自 dsh-design-mode `packages/client/ui-design-mode/src/client/controller.ts`
 * （MIT © DeepSeek），去 cordis 化：Remote 调用 → 插件 ctx.rest，observable →
 * nanostores atom。核心纪律（勿在后续改动中丢失）：
 * - **changeDocument(fn, undoable)** 是唯一的文档变更入口：undoable 的进撤销栈；
 *   viewport 这类高频抖动不进栈（dsh 同款）。
 * - **保存串行化**：flushSave 挂在 operationTail promise 链上，任意时刻只有一发
 *   PUT 在途；链内取"当时最新"文档（coalescing），中间态不会互相踩 revision。
 * - **幂等保存**：同一批未落盘编辑共用一个 operation_id，网络重试命中 Host 重放。
 * - **hide 不删**：Delete 键只隐藏元素（版本与资产保留），防误删生成结果。
 * - **assetUrl 缓存**：b64→objectURL 每资产只取一次。
 */
import { atom } from 'nanostores'

import { fetchAssetUrl } from './api'
import type { CanvasDocument, CanvasTool, CanvasViewport, DesignAsset, DesignJob } from './types'

export interface DesignView {
  busy: boolean
  document: CanvasDocument | null
  error: null | string
  pid: null | string
  savedRevision: null | number
  selectedIds: readonly string[]
  status: 'cold' | 'loading' | 'ready'
  tool: CanvasTool
}

export const $designView = atom<DesignView>({
  busy: false,
  document: null,
  error: null,
  pid: null,
  savedRevision: null,
  selectedIds: [],
  status: 'cold',
  tool: 'select'
})

type Rest = <T>(path: string, opts?: { body?: unknown; method?: string; timeoutMs?: number }) => Promise<T>

let rest: null | Rest = null

export function bindController(r: Rest): void {
  rest = r
}

function needRest<T>(path: string, opts?: { body?: unknown; method?: string; timeoutMs?: number }): Promise<T> {
  if (!rest) {
    throw new Error('gugu-design api not bound')
  }

  return rest<T>(path, opts)
}

const SAVE_DEBOUNCE_MS = 800
const UNDO_LIMIT = 50
/** 多选上限（dsh 同值）：再多会让 Transformer/上下文芯片失去意义。 */
const SELECT_LIMIT = 14

const clone = (doc: CanvasDocument): CanvasDocument => structuredClone(doc)

class DesignController {
  private undoStack: CanvasDocument[] = []
  private redoStack: CanvasDocument[] = []
  private saveTimer: ReturnType<typeof setTimeout> | null = null
  // 保存串行链（dsh operationTail）：所有 PUT 依次排队，链内取最新文档。
  private operationTail: Promise<void> = Promise.resolve()
  // 幂等保存：同一批未落盘编辑共用一个 operation_id，重试命中 Host 重放而非 409。
  private pendingOpId: string | null = null

  get view(): DesignView {
    return $designView.get()
  }

  private publish(patch: Partial<DesignView>): void {
    $designView.set({ ...$designView.get(), ...patch })
  }

  // ---- 生命周期 -----------------------------------------------------------

  async open(pid: string): Promise<void> {
    this.publish({ busy: true, error: null, pid, status: 'loading' })
    this.undoStack = []
    this.redoStack = []

    try {
      const { document } = await needRest<{ document: CanvasDocument }>(`/projects/${pid}`)

      // 规范化：M2b 之前的旧工程文档没有 comments 字段，补空对象防渲染层崩。
      document.comments ??= {}
      this.publish({ busy: false, document, savedRevision: document.revision, selectedIds: [], status: 'ready' })
    } catch (error) {
      this.publish({ busy: false, error: String(error), status: 'cold' })
    }
  }

  close(): void {
    if (this.saveTimer !== null) {
      clearTimeout(this.saveTimer)
    }

    this.publish({ document: null, pid: null, savedRevision: null, selectedIds: [], status: 'cold' })
  }

  // ---- 变更核心（dsh changeDocument 语义） ---------------------------------

  /**
   * 唯一文档变更入口。
   * @param fn 就地修改深拷贝后的文档
   * @param undoable 是否进撤销栈（viewport 抖动传 false）
   */
  private changeDocument(fn: (doc: CanvasDocument) => void, undoable = true): void {
    const doc = this.view.document

    if (!doc) {
      return
    }

    if (undoable) {
      this.undoStack.push(clone(doc))

      if (this.undoStack.length > UNDO_LIMIT) {
        this.undoStack.shift()
      }

      this.redoStack = []
    }

    const next = clone(doc)
    fn(next)
    next.revision = doc.revision + 1
    next.updatedAt = Date.now() / 1000
    this.publish({ document: next })
    this.scheduleSave()
  }

  private scheduleSave(): void {
    if (this.saveTimer !== null) {
      clearTimeout(this.saveTimer)
    }

    this.saveTimer = setTimeout(() => this.enqueueSave(), SAVE_DEBOUNCE_MS)
  }

  /** 把一次保存挂到串行链尾；链内取"当时最新"文档（coalescing）。 */
  private enqueueSave(): void {
    this.operationTail = this.operationTail.then(() => this.flushSave()).catch(() => undefined)
  }

  private async flushSave(): Promise<void> {
    const { document: doc, savedRevision, pid } = this.view

    if (!doc || !pid) {
      return
    }

    this.pendingOpId ??= `save-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
    this.publish({ busy: true })

    try {
      const res = await needRest<{ document: CanvasDocument }>(`/projects/${pid}/document`, {
        body: { document: doc, expected_revision: savedRevision ?? doc.revision - 1, operation_id: this.pendingOpId },
        method: 'PUT'
      })
      this.pendingOpId = null
      // Host 合并后返回权威文档；revision 对齐后继续本地编辑。
      this.publish({ busy: false, document: res.document, savedRevision: res.document.revision })
    } catch (error) {
      this.publish({ busy: false, error: '保存冲突或失败：' + String(error) })
      // 冲突后拉回权威版本（本地未保存编辑丢弃 —— MVP 单用户场景极少发生）
      await this.open(pid)
    }
  }

  // ---- 元素与视口 ----------------------------------------------------------

  /** 追加一个画布元素（素材落画布：居中于当前视口，z 取顶）。 */
  addElement(assetId: string): void {
    const { document: doc } = this.view

    if (!doc) {
      return
    }

    const asset = doc.assets[assetId]

    if (!asset) {
      return
    }

    const vp = doc.viewport
    const cx = (window.innerWidth / 2 - vp.x) / vp.scale
    const cy = (window.innerHeight / 2 - vp.y) / vp.scale
    const width = asset.width || 512
    const height = asset.height || Math.round(width * (asset.kind === 'video' ? 9 / 16 : 1))
    const id = Math.random().toString(36).slice(2, 12)
    const z = Object.values(doc.elements).reduce((m, e) => Math.max(m, e.z), 0) + 1
    this.changeDocument(d => {
      d.elements[id] = {
        assetId, height, hidden: false, id, locked: false, rotation: 0,
        type: asset.kind, versionId: asset.versionId ?? '', width, x: cx - width / 2, y: cy - height / 2, z
      }
    })
    this.publish({ selectedIds: [id] })
  }

  updateElement(
    id: string,
    patch: Partial<Pick<CanvasDocument['elements'][string], 'height' | 'locked' | 'rotation' | 'width' | 'x' | 'y'>>
  ): void {
    this.changeDocument(d => {
      const el = d.elements[id]

      if (el && !el.locked) {
        Object.assign(el, patch)
      }
    })
  }

  updateViewport(viewport: CanvasViewport): void {
    // 视口抖动不进撤销栈（dsh 同款：undo 不该回退"看哪里"）。
    this.changeDocument(d => {
      d.viewport = viewport
    }, false)
  }

  /** Delete 键语义 = 隐藏，不是删除（版本与资产保留，可 fitToView 找回视野）。 */
  hideSelected(): void {
    this.changeDocument(d => {
      for (const id of this.view.selectedIds) {
        const el = d.elements[id]

        if (el) {
          el.hidden = true
        }
      }
    })
    this.clearSelection()
  }

  toggleSelectedLock(): void {
    this.changeDocument(d => {
      for (const id of this.view.selectedIds) {
        const el = d.elements[id]

        if (el) {
          el.locked = !el.locked
        }
      }
    })
  }

  bringSelectedForward(): void {
    this.changeDocument(d => {
      const maxZ = Object.values(d.elements).reduce((m, e) => Math.max(m, e.z), 0)

      for (const id of this.view.selectedIds) {
        const el = d.elements[id]

        if (el) {
          el.z = maxZ + 1
        }
      }
    })
  }

  /** 把所有可见元素框进视口（留 120px 呼吸边）。 */
  fitToView(width: number, height: number): void {
    const { document: doc } = this.view

    if (!doc) {
      return
    }

    const elements = Object.values(doc.elements).filter(e => !e.hidden)

    if (elements.length === 0) {
      return
    }

    const minX = Math.min(...elements.map(e => e.x))
    const minY = Math.min(...elements.map(e => e.y))
    const maxX = Math.max(...elements.map(e => e.x + e.width))
    const maxY = Math.max(...elements.map(e => e.y + e.height))
    const scale = Math.min(1.5, Math.max(0.08, Math.min((width - 120) / (maxX - minX), (height - 120) / (maxY - minY))))
    this.updateViewport({ scale, x: (width - (minX + maxX) * scale) / 2, y: (height - (minY + maxY) * scale) / 2 })
  }

  // ---- 选择与工具 ----------------------------------------------------------

  select(id: string, additive = false): void {
    const cur = this.view.selectedIds
    let selected: string[]

    if (!additive) {
      selected = [id]
    } else if (cur.includes(id)) {
      selected = cur.filter(x => x !== id)
    } else {
      selected = [...cur, id].slice(0, SELECT_LIMIT)
    }

    this.publish({ selectedIds: selected })
  }

  clearSelection(): void {
    this.publish({ selectedIds: [] })
  }

  setTool(tool: CanvasTool): void {
    this.publish({ tool })
  }

  clearError(): void {
    this.publish({ error: null })
  }

  // ---- 注释锚点（送会话流程在 M4） -----------------------------------------

  addPointComment(versionId: string, x: number, y: number): void {
    const now = Date.now() / 1000
    const id = Math.random().toString(36).slice(2, 12)
    this.changeDocument(d => {
      d.comments[id] = {
        anchor: { x, y, kind: 'point' }, createdAt: now, id, parentVersionId: versionId,
        status: 'draft', thread: [], updatedAt: now
      }
    })
  }

  addRectComment(versionId: string, x: number, y: number, width: number, height: number): void {
    const now = Date.now() / 1000
    const id = Math.random().toString(36).slice(2, 12)
    this.changeDocument(d => {
      d.comments[id] = {
        anchor: { height, kind: 'rect', width, x, y }, createdAt: now, id, parentVersionId: versionId,
        status: 'draft', thread: [], updatedAt: now
      }
    })
  }

  // ---- 任务/素材登记（api 轮询层调用） --------------------------------------

  ingestJob(job: DesignJob, assets: Record<string, DesignAsset>, dropOnCanvas: boolean): void {
    this.changeDocument(d => {
      d.jobs[job.id] = job

      for (const aid of job.outputAssetIds) {
        const asset = assets[aid]

        if (asset) {
          d.assets[aid] = asset

          if (asset.versionId && !d.versions[asset.versionId]) {
            d.versions[asset.versionId] = {
              assetId: aid, createdAt: asset.createdAt, id: asset.versionId, jobId: job.id, label: asset.name
            }
          }
        }
      }
    }, false)

    if (dropOnCanvas) {
      for (const aid of job.outputAssetIds) {
        if (!Object.values(this.view.document?.elements ?? {}).some(e => e.assetId === aid)) {
          this.addElement(aid)
        }
      }
    }
  }

  // ---- undo/redo（快照栈） --------------------------------------------------

  undo(): void {
    const prev = this.undoStack.pop()

    if (!prev) {
      return
    }

    const cur = this.view.document

    if (cur) {
      this.redoStack.push(clone(cur))
    }

    this.publish({ document: { ...prev, revision: (cur?.revision ?? prev.revision) + 1 } })
    this.scheduleSave()
  }

  redo(): void {
    const next = this.redoStack.pop()

    if (!next) {
      return
    }

    const cur = this.view.document

    if (cur) {
      this.undoStack.push(clone(cur))
    }

    this.publish({ document: { ...next, revision: (cur?.revision ?? next.revision) + 1 } })
    this.scheduleSave()
  }

  get canUndo(): boolean {
    return this.undoStack.length > 0
  }

  get canRedo(): boolean {
    return this.redoStack.length > 0
  }

  // ---- 资产 URL（缓存代理） --------------------------------------------------

  private readonly assetUrls = new Map<string, Promise<string>>()

  /** asset → objectURL（b64 通道，每资产一次；dsh assetUrl 语义）。 */
  assetUrl(assetId: string): Promise<string> {
    const pid = this.view.pid ?? ''
    const key = `${pid}:${assetId}`
    let pending = this.assetUrls.get(key)

    if (!pending) {
      pending = fetchAssetUrl(pid, assetId)
      this.assetUrls.set(key, pending)
    }

    return pending
  }
}

export const designController = new DesignController()
