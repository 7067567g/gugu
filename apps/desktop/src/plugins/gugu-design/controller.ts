/**
 * gugu-design 会话控制器 —— 本地乐观文档状态 + Host 权威持久化。
 * 架构移植自 dsh-design-mode 的 DesignSessionController（简化版）：
 * - 所有 mutation 走 `mutate()`：本地改 → revision++ → 800ms 防抖保存；
 * - 保存冲突（409）→ 拉回权威文档并提示（MVP 策略：本地丢弃，重放选中）；
 * - undo/redo 用整文档快照栈（文档小，快照最省心）。
 */
import { atom } from 'nanostores'

import type { CanvasDocument, CanvasElement, DesignAsset, DesignJob } from './types'

export type DesignTool = 'hand' | 'select'

export interface DesignView {
  busy: boolean
  document: CanvasDocument | null
  error: null | string
  pid: null | string
  savedRevision: null | number
  selectedIds: readonly string[]
  status: 'cold' | 'loading' | 'ready'
  tool: DesignTool
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

class DesignController {
  private undoStack: CanvasDocument[] = []
  private redoStack: CanvasDocument[] = []
  private saveTimer: ReturnType<typeof setTimeout> | null = null
  private saving = false
  private dirty = false

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
      this.publish({ busy: false, document, savedRevision: document.revision, status: 'ready' })
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

  // ---- mutation 核心 ------------------------------------------------------

  private mutate(fn: (doc: CanvasDocument) => void, opts: { history?: boolean } = { history: true }): void {
    const { document: doc } = this.view

    if (!doc) {
      return
    }

    if (opts.history !== false) {
      this.undoStack.push(structuredClone(doc))

      if (this.undoStack.length > UNDO_LIMIT) {
        this.undoStack.shift()
      }

      this.redoStack = []
    }

    const next = structuredClone(doc)
    fn(next)
    next.revision = doc.revision + 1
    next.updatedAt = Date.now() / 1000
    this.publish({ document: next })
    this.scheduleSave()
  }

  private scheduleSave(): void {
    this.dirty = true

    if (this.saveTimer !== null) {
      clearTimeout(this.saveTimer)
    }

    this.saveTimer = setTimeout(() => void this.flushSave(), SAVE_DEBOUNCE_MS)
  }

  async flushSave(): Promise<void> {
    if (this.saving || !this.dirty) {
      return
    }

    const { document: doc, savedRevision, pid } = this.view

    if (!doc || !pid) {
      return
    }

    this.saving = true
    this.dirty = false
    this.publish({ busy: true })

    try {
      const res = await needRest<{ document: CanvasDocument }>('/projects/' + pid + '/document', {
        body: { document: doc, expected_revision: savedRevision ?? doc.revision - 1 },
        method: 'PUT'
      })
      this.publish({ busy: false, document: res.document, savedRevision: res.document.revision })
    } catch (error) {
      this.publish({ busy: false, error: '保存冲突或失败：' + String(error) })
      // 冲突后拉回权威版本（本地未保存编辑丢弃 —— MVP 单用户场景极少发生）
      await this.open(pid)
    } finally {
      this.saving = false

      if (this.dirty) {
        this.scheduleSave()
      }
    }
  }

  // ---- 元素操作 -----------------------------------------------------------

  addElement(assetId: string, x: number, y: number): void {
    this.mutate(doc => {
      const asset = doc.assets[assetId]

      if (!asset) {
        return
      }

      const id = Math.random().toString(36).slice(2, 12)
      const z = Object.values(doc.elements).reduce((m, e) => Math.max(m, e.z), 0) + 1
      const width = asset.width || 512
      const height = asset.height || Math.round(width * (asset.kind === 'video' ? 9 / 16 : 1))
      const el: CanvasElement = {
        assetId,
        height,
        hidden: false,
        id,
        locked: false,
        rotation: 0,
        type: asset.kind,
        versionId: asset.versionId ?? '',
        width,
        x: x - width / 2,
        y: y - height / 2,
        z
      }
      doc.elements[id] = el
      this.publish({ selectedIds: [id] })
    })
  }

  moveElement(id: string, x: number, y: number): void {
    this.mutate(doc => {
      const el = doc.elements[id]

      if (el && !el.locked) {
        el.x = x
        el.y = y
      }
    })
  }

  resizeElement(id: string, width: number, height: number): void {
    this.mutate(doc => {
      const el = doc.elements[id]

      if (el && !el.locked) {
        el.width = Math.max(16, width)
        el.height = Math.max(16, height)
      }
    })
  }

  removeSelected(): void {
    const { selectedIds } = this.view

    if (selectedIds.length === 0) {
      return
    }

    this.mutate(doc => {
      for (const id of selectedIds) {
        delete doc.elements[id]
      }
    })
    this.publish({ selectedIds: [] })
  }

  bringSelectedForward(): void {
    this.mutate(doc => {
      const maxZ = Object.values(doc.elements).reduce((m, e) => Math.max(m, e.z), 0)

      for (const id of this.view.selectedIds) {
        const el = doc.elements[id]

        if (el) {
          el.z = maxZ + 1
        }
      }
    })
  }

  setViewport(scale: number, x: number, y: number): void {
    // 视口移动不进撤销栈（history:false），保存仍触发
    this.mutate(doc => {
      doc.viewport = { scale, x, y }
    }, { history: false })
  }

  select(ids: readonly string[]): void {
    this.publish({ selectedIds: ids })
  }

  setTool(tool: DesignTool): void {
    this.publish({ tool })
  }

  clearError(): void {
    this.publish({ error: null })
  }

  // ---- 任务/素材登记（由 api 层轮询调用） ----------------------------------

  ingestJob(job: DesignJob, assets: Record<string, DesignAsset>, dropOnCanvas: boolean): void {
    this.mutate(doc => {
      doc.jobs[job.id] = job

      for (const aid of job.outputAssetIds) {
        const asset = assets[aid]

        if (asset) {
          doc.assets[aid] = asset

          if (asset.versionId && !doc.versions[asset.versionId]) {
            doc.versions[asset.versionId] = {
              assetId: aid,
              createdAt: asset.createdAt,
              id: asset.versionId,
              jobId: job.id,
              label: asset.name
            }
          }
        }
      }
    })

    // 产物自动落画布中心（前端放置，学 dsh-design-mode：结果写回当前画布）
    if (dropOnCanvas) {
      const { document: doc } = this.view
      const vp = doc?.viewport ?? { scale: 1, x: 0, y: 0 }
      const cx = (window.innerWidth / 2 - vp.x) / vp.scale
      const cy = (window.innerHeight / 2 - vp.y) / vp.scale

      for (const aid of job.outputAssetIds) {
        if (!Object.values(doc?.elements ?? {}).some(e => e.assetId === aid)) {
          this.addElement(aid, cx, cy)
        }
      }
    }
  }

  // ---- undo/redo ----------------------------------------------------------

  undo(): void {
    const prev = this.undoStack.pop()

    if (!prev) {
      return
    }

    const { document: cur } = this.view

    if (cur) {
      this.redoStack.push(structuredClone(cur))
    }

    const doc = { ...prev, revision: (cur?.revision ?? prev.revision) + 1 }
    this.publish({ document: doc })
    this.scheduleSave()
  }

  redo(): void {
    const next = this.redoStack.pop()

    if (!next) {
      return
    }

    const { document: cur } = this.view

    if (cur) {
      this.undoStack.push(structuredClone(cur))
    }

    const doc = { ...next, revision: (cur?.revision ?? next.revision) + 1 }
    this.publish({ document: doc })
    this.scheduleSave()
  }

  get canUndo(): boolean {
    return this.undoStack.length > 0
  }

  get canRedo(): boolean {
    return this.redoStack.length > 0
  }
}

export const designController = new DesignController()
