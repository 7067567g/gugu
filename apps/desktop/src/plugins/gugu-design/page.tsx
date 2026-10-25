/** 咕咕设计页：工程列表 → 无限画布（工具栏 + 生成面板 + 任务列表）。 */
import { Button, Codicon, Input, cn, host, useValue } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useRef, useState } from 'react'

import {
  dismissGuidance,
  downloadCanvasExport,
  exportCanvas,
  fetchGuidance,
  ingestCompletedJobs,
  uploadAsset,
  useCreateProject,
  useDeleteProject,
  useGatewayModels,
  useGatewayStatus,
  useJobs,
  useProjects,
  useSubmitJob
} from './api'
import { DesignCanvas, type PendingCommentAnchor } from './canvas'
import { $designView, designController } from './controller'
import { guidanceLayer } from './guidance'
import { t } from './i18n'
import type { CanvasTool } from './types'

function ProjectPicker({ onOpen }: { onOpen: (pid: string) => void }) {
  const { data, isLoading } = useProjects()
  const [name, setName] = useState('')
  const create = useCreateProject(onOpen)
  const del = useDeleteProject()
  const projects = data?.projects ?? []

  return (
    <div
      className="mx-auto flex h-full w-full max-w-xl flex-col gap-4 overflow-y-auto p-8"
      data-agent-id="design.projects"
    >
      <h1 className="text-xl font-semibold">{t('title')}</h1>
      <div className="flex gap-2">
        <Input
          className="flex-1"
          data-agent-id="design.projects.name"
          onChange={e => setName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && name.trim() && create.mutate(name.trim())}
          placeholder={t('newProjectPlaceholder')}
          value={name}
        />
        <Button
          data-agent-id="design.projects.create"
          disabled={!name.trim() || create.isPending}
          onClick={() => create.mutate(name.trim())}
        >
          {t('create')}
        </Button>
      </div>
      {isLoading
        ? <p className="text-sm opacity-60">{t('loading')}</p>
        : projects.length === 0
          ? <p className="text-sm opacity-60">{t('noProjects')}</p>
          : (
              <ul className="grid gap-2">
                {projects.map(p => (
                  <li className="flex items-center justify-between rounded-lg border border-(--ui-stroke-secondary) px-3 py-2" key={p.id}>
                    <button
                      className="min-w-0 flex-1 text-left"
                      onClick={() => onOpen(p.id)}
                      type="button"
                    >
                      <div className="truncate text-sm font-medium">{p.name}</div>
                      <div className="text-xs opacity-60">{t('assetCount', { n: p.assets })}</div>
                    </button>
                    <Button
                      onClick={() => {
                        if (window.confirm(t('confirmDelete', { name: p.name }))) {
                          del.mutate(p.id)
                        }
                      }}
                      size="icon-sm"
                      variant="ghost"
                    >
                      <Codicon name="trash" />
                    </Button>
                  </li>
                ))}
              </ul>
            )}
    </div>
  )
}

function GeneratePanel({ pid }: { pid: string }) {
  const [kind, setKind] = useState<'image' | 'video'>('image')
  const [prompt, setPrompt] = useState('')
  const [model, setModel] = useState('')
  const [size, setSize] = useState('1024x1024')
  const [duration, setDuration] = useState(5)
  const submit = useSubmitJob(pid)
  const gw = useGatewayStatus()
  const models = useGatewayModels(kind)
  const modelOptions = models.data?.models ?? []

  const send = () => {
    if (!prompt.trim()) {
      return
    }

    const request: Record<string, unknown> = { prompt: prompt.trim() }

    if (model.trim()) {
      request.model = model.trim()
    }

    if (kind === 'image') {
      request.size = size
    } else {
      request.duration = duration
    }

    submit.mutate({ kind, request }, { onSuccess: () => setPrompt('') })
  }

  if (gw.data && !gw.data.configured) {
    return (
      <div className="grid gap-2 p-4 text-sm" data-agent-id="design.panel">
        <p className="font-medium">{t('notLoggedIn')}</p>
        <Button onClick={() => host.navigate('/settings?tab=providers')}>{t('goLogin')}</Button>
      </div>
    )
  }

  return (
    <div className="grid content-start gap-3 p-4" data-agent-id="design.panel">
      <div className="flex gap-1 rounded-lg bg-(--ui-control-hover-background) p-1" data-agent-id="design.composer.kind">
        {(['image', 'video'] as const).map(k => (
          <button
            className={cn(
              'flex-1 rounded-md px-2 py-1 text-sm transition-colors',
              kind === k ? 'bg-(--ui-chat-bubble-background) font-medium shadow-sm' : 'opacity-70'
            )}
            key={k}
            onClick={() => {
              setKind(k)
              // 模式切换是引导的硬中断源：换了模态，之前指的控件含义就变了。
              guidanceLayer.interrupt('mode')
            }}
            type="button"
          >
            {t(k === 'image' ? 'tabImage' : 'tabVideo')}
          </button>
        ))}
      </div>
      <textarea
        className="min-h-24 w-full resize-y rounded-lg border border-(--ui-stroke-secondary) bg-transparent p-2 text-sm outline-none focus:border-primary"
        data-agent-id="design.composer"
        onChange={e => setPrompt(e.target.value)}
        placeholder={t('promptPlaceholder')}
        value={prompt}
      />
      <select
        className="w-full rounded-md border border-(--ui-stroke-secondary) bg-transparent px-2 py-1.5 text-sm"
        onChange={e => setModel(e.target.value)}
        value={model}
      >
        <option value="">{t('modelDefaultOption')}</option>
        {modelOptions.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
      </select>
      {kind === 'image'
        ? (
            <select
              className="rounded-md border border-(--ui-stroke-secondary) bg-transparent px-2 py-1.5 text-sm"
              onChange={e => setSize(e.target.value)}
              value={size}
            >
              {['1024x1024', '1536x1024', '1024x1536'].map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          )
        : (
            <label className="flex items-center gap-2 text-sm">
              {t('duration')}
              <input
                max={15}
                min={4}
                onChange={e => setDuration(Number(e.target.value))}
                type="range"
                value={duration}
              />
              {duration}s
            </label>
          )}
      <Button data-agent-id="design.composer.send" disabled={!prompt.trim() || submit.isPending} onClick={send}>
        {submit.isPending ? t('submitting') : t('generate')}
      </Button>
      {submit.isError && <p className="text-xs text-red-500">{String(submit.error)}</p>}
    </div>
  )
}

function JobList({ pid }: { pid: string }) {
  const { data } = useJobs(pid)
  const jobs = [...(data?.jobs ?? [])].sort((a, b) => b.createdAt - a.createdAt).slice(0, 12)
  const view = useValue($designView)

  // job 完成 → 拉 asset 落画布
  const ingestedRef = useRef(new Set<string>())

  useEffect(() => {
    for (const job of jobs) {
      if (job.status === 'succeeded' && !ingestedRef.current.has(job.id)) {
        ingestedRef.current.add(job.id)
        void ingestCompletedJobs(pid, [job], view.document)
      }
    }
  }, [jobs, pid, view.document])

  if (jobs.length === 0) {
    return null
  }

  return (
    <ul className="grid gap-1 border-t border-(--ui-stroke-secondary) p-3 text-xs" data-agent-id="design.panel.jobs">
      {jobs.map(j => (
        <li className="flex items-center justify-between gap-2" key={j.id}>
          <span className="truncate opacity-80">{String(j.request?.prompt ?? j.id).slice(0, 28)}</span>
          <span className={cn(
            j.status === 'succeeded' && 'text-emerald-500',
            j.status === 'failed' && 'text-red-500',
            (j.status === 'queued' || j.status === 'running') && 'opacity-60'
          )}>
            {j.status === 'running' && j.progress !== undefined ? `${j.progress}%` : t(`job.${j.status}`)}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** 画布工具组（select/hand/注释锚点）。注释→会话送审流程在 M4 接入。 */
const TOOLS: Array<{ icon: 'edit' | 'grab' | 'comment' | 'comment-discussion'; id: CanvasTool; key: string }> = [
  { icon: 'edit', id: 'select', key: 'toolSelect' },
  { icon: 'grab', id: 'hand', key: 'toolHand' },
  { icon: 'comment', id: 'point-comment', key: 'toolPoint' },
  { icon: 'comment-discussion', id: 'rect-comment', key: 'toolRect' }
]

function Workspace({ onBack }: { onBack: () => void }) {
  const view = useValue($designView)
  const fileRef = useRef<HTMLInputElement>(null)
  const canvasBoxRef = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState({ h: 600, w: 900 })
  const [exporting, setExporting] = useState(false)
  const pid = view.pid ?? ''
  const doc = view.document
  const hasSelection = view.selectedIds.length > 0

  // 容器尺寸：canvas 需要显式宽高（dsh 同款，由 Surface 测量传入）。
  useEffect(() => {
    const el = canvasBoxRef.current

    if (!el) {
      return
    }

    const ro = new ResizeObserver(() => setBox({ h: el.clientHeight, w: el.clientWidth }))

    ro.observe(el)

    return () => ro.disconnect()
  }, [view.status])

  // M2 引导层：后端没有 renderer 方向的 host 通道（M1 剩余），这里用约 1.2s
  // 轮询消费 designs/guidance.json；本地中断由 manager 负责，退出时回报清空邮箱。
  useEffect(() => {
    let stopped = false
    let inFlight = false
    const unsubscribe = guidanceLayer.subscribe(state => {
      if (state.status === 'idle' && state.last_end) {
        void dismissGuidance(state.last_end.guidance_id)
      }
    })

    const tick = async () => {
      if (stopped || inFlight) {
        return
      }
      inFlight = true
      try {
        const command = await fetchGuidance()
        if (stopped || !command || guidanceLayer.activeId() === command.guidance_id) {
          return
        }
        try {
          guidanceLayer.show({
            id: command.guidance_id,
            style: command.style,
            target_anchor: command.target_anchor,
            text: command.text ?? '',
            timeoutMs: command.timeout_ms ?? 0
          })
        } catch {
          // 目标不在当前 DOM（例如面板被折叠）：不留假状态，直接清掉这一步。
          void dismissGuidance(command.guidance_id)
        }
      } catch {
        // 后端未就绪 / 远端路由没有插件 API：下次轮询再试，不打扰画布。
      } finally {
        inFlight = false
      }
    }

    const poll = window.setInterval(() => void tick(), 1200)
    void tick()

    return () => {
      stopped = true
      window.clearInterval(poll)
      // 先 dispose（会发 idle，订阅者删邮箱），再退订阅。
      guidanceLayer.dispose()
      unsubscribe()
    }
  }, [])

  const upload = useCallback(async (files: FileList | null) => {
    if (!files) {
      return
    }

    for (const file of Array.from(files)) {
      try {
        const asset = await uploadAsset(pid, file)
        designController.addElement(asset.id)
      } catch (error) {
        window.alert(String(error))
      }
    }
  }, [pid])

  const onExport = useCallback(async () => {
    if (!pid || exporting) {
      return
    }

    setExporting(true)

    try {
      downloadCanvasExport(await exportCanvas(pid))
    } catch (error) {
      window.alert(String(error))
    } finally {
      setExporting(false)
    }
  }, [exporting, pid])

  const onComment = useCallback((anchor: PendingCommentAnchor) => {
    // M2b：锚点先落文档（draft）；送会话处理在 M4 接 composer。
    if (anchor.kind === 'point') {
      designController.addPointComment(anchor.versionId, anchor.x, anchor.y)
    } else {
      designController.addRectComment(anchor.versionId, anchor.x, anchor.y, anchor.width ?? 0, anchor.height ?? 0)
    }
    designController.setTool('select')
  }, [])

  if (view.status !== 'ready' || !doc) {
    return <div className="grid place-items-center text-sm opacity-60">{view.error ?? t('loading')}</div>
  }

  return (
    <div className="flex h-full min-h-0" data-agent-id="design.workspace">
      <div className="relative min-w-0 flex-1">
        <div ref={canvasBoxRef} className="absolute inset-0 overflow-hidden" data-agent-id="design.canvas">
          <DesignCanvas
            document={doc}
            height={box.h}
            onComment={onComment}
            selectedIds={view.selectedIds}
            tool={view.tool}
            width={box.w}
          />
        </div>
        {/* 顶部悬浮工具条：导航 + 撤销 + 工具组 + 元素操作 */}
        <div className="absolute left-3 top-3 z-10 flex items-center gap-1 rounded-xl border border-(--ui-stroke-secondary) bg-(--ui-chat-bubble-background)/90 px-2 py-1.5 shadow-lg backdrop-blur">
          <Button onClick={onBack} size="icon-sm" variant="ghost"><Codicon name="chevron-left" /></Button>
          <span className="max-w-32 truncate px-1 text-sm font-medium">{doc.name}</span>
          <span className="px-1 text-[11px] opacity-50">{view.busy ? t('saving') : t('saved')}</span>
          <span className="mx-1 h-4 w-px bg-(--ui-stroke-secondary)" />
          <Button data-agent-id="design.canvas.undo" disabled={!designController.canUndo} onClick={() => designController.undo()} size="icon-sm" variant="ghost"><Codicon name="discard" /></Button>
          <Button data-agent-id="design.canvas.redo" disabled={!designController.canRedo} onClick={() => designController.redo()} size="icon-sm" variant="ghost"><Codicon name="go-to-start" /></Button>
          <span className="mx-1 h-4 w-px bg-(--ui-stroke-secondary)" />
          {TOOLS.map(tool => (
            <Button
              className={cn(view.tool === tool.id && 'bg-primary/15 text-primary')}
              key={tool.id}
              onClick={() => designController.setTool(tool.id)}
              size="icon-sm"
              title={t(tool.key)}
              variant="ghost"
            >
              <Codicon name={tool.icon} />
            </Button>
          ))}
          <span className="mx-1 h-4 w-px bg-(--ui-stroke-secondary)" />
          <Button disabled={!hasSelection} onClick={() => designController.toggleSelectedLock()} size="icon-sm" variant="ghost" title={t('toolLock')}>
            <Codicon name={hasSelection && doc.elements[view.selectedIds[0]]?.locked ? 'lock' : 'lock-small'} />
          </Button>
          <Button disabled={!hasSelection} onClick={() => designController.bringSelectedForward()} size="icon-sm" variant="ghost" title={t('toolForward')}>
            <Codicon name="arrow-up" />
          </Button>
          <Button data-agent-id="design.canvas.fit" disabled={Object.keys(doc.elements).length === 0} onClick={() => designController.fitToView(box.w, box.h)} size="icon-sm" variant="ghost" title={t('toolFit')}>
            <Codicon name="zoom-in" />
          </Button>
          <Button data-agent-id="design.canvas.upload" onClick={() => fileRef.current?.click()} size="icon-sm" variant="ghost" title={t('toolUpload')}><Codicon name="add" /></Button>
          <Button data-agent-id="design.canvas.hide" disabled={!hasSelection} onClick={() => designController.hideSelected()} size="icon-sm" variant="ghost" title={t('toolHide')}>
            <Codicon name="trash" />
          </Button>
          <Button
            data-agent-id="canvas.export"
            disabled={exporting || Object.keys(doc.elements).length === 0}
            onClick={() => void onExport()}
            size="icon-sm"
            title={t('toolExport')}
            variant="ghost"
          >
            <Codicon name="cloud-download" />
          </Button>
          <span className="px-1 text-xs tabular-nums opacity-60">{Math.round((doc.viewport.scale ?? 1) * 100)}%</span>
          <input
            accept="image/*,video/mp4"
            className="hidden"
            multiple
            onChange={e => void upload(e.target.files)}
            ref={fileRef}
            type="file"
          />
        </div>
        {view.tool !== 'select' && view.tool !== 'hand' && (
          <div className="absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded-full bg-primary/90 px-3 py-1 text-xs text-white shadow-lg">
            {t(view.tool === 'point-comment' ? 'hintPoint' : 'hintRect')}
          </div>
        )}
        {view.error && (
          <button
            className="absolute bottom-3 left-3 z-10 rounded-lg bg-red-500/90 px-3 py-1.5 text-xs text-white"
            onClick={() => designController.clearError()}
            type="button"
          >
            {view.error}
          </button>
        )}
      </div>
      <aside className="w-72 shrink-0 overflow-y-auto border-l border-(--ui-stroke-secondary)">
        <GeneratePanel pid={pid} />
        <JobList pid={pid} />
      </aside>
    </div>
  )
}

export function DesignPage() {
  const view = useValue($designView)

  const open = useCallback((pid: string) => {
    void designController.open(pid)
  }, [])

  return view.pid && view.status !== 'cold'
    ? <Workspace onBack={() => designController.close()} />
    : <ProjectPicker onOpen={open} />
}
