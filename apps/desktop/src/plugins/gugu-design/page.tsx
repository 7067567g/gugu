/** 咕咕设计页：工程列表 → 无限画布 + 生成面板（图/视频任务）。 */
import { Button, Codicon, Input, cn, host, useValue } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useRef, useState } from 'react'

import {
  designController,
  $designView
} from './controller'
import {
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
import { DesignCanvas } from './canvas'
import { t } from './i18n'

function ProjectPicker({ onOpen }: { onOpen: (pid: string) => void }) {
  const { data, isLoading } = useProjects()
  const [name, setName] = useState('')
  const create = useCreateProject(onOpen)
  const del = useDeleteProject()
  const projects = data?.projects ?? []

  return (
    <div className="mx-auto flex h-full w-full max-w-xl flex-col gap-4 overflow-y-auto p-8">
      <h1 className="text-xl font-semibold">{t('title')}</h1>
      <div className="flex gap-2">
        <Input
          className="flex-1"
          onChange={e => setName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && name.trim() && create.mutate(name.trim())}
          placeholder={t('newProjectPlaceholder')}
          value={name}
        />
        <Button disabled={!name.trim() || create.isPending} onClick={() => create.mutate(name.trim())}>
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
      <div className="grid gap-2 p-4 text-sm">
        <p className="font-medium">{t('notLoggedIn')}</p>
        <Button onClick={() => host.navigate('/settings?tab=providers')}>{t('goLogin')}</Button>
      </div>
    )
  }

  return (
    <div className="grid content-start gap-3 p-4">
      <div className="flex gap-1 rounded-lg bg-(--ui-control-hover-background) p-1">
        {(['image', 'video'] as const).map(k => (
          <button
            className={cn(
              'flex-1 rounded-md px-2 py-1 text-sm transition-colors',
              kind === k ? 'bg-(--ui-chat-bubble-background) font-medium shadow-sm' : 'opacity-70'
            )}
            key={k}
            onClick={() => setKind(k)}
            type="button"
          >
            {t(k === 'image' ? 'tabImage' : 'tabVideo')}
          </button>
        ))}
      </div>
      <textarea
        className="min-h-24 w-full resize-y rounded-lg border border-(--ui-stroke-secondary) bg-transparent p-2 text-sm outline-none focus:border-primary"
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
      <Button disabled={!prompt.trim() || submit.isPending} onClick={send}>
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
    <ul className="grid gap-1 border-t border-(--ui-stroke-secondary) p-3 text-xs">
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

function Workspace({ onBack }: { onBack: () => void }) {
  const view = useValue($designView)
  const fileRef = useRef<HTMLInputElement>(null)
  const pid = view.pid ?? ''

  const upload = useCallback(async (files: FileList | null) => {
    if (!files) {
      return
    }

    for (const file of Array.from(files)) {
      try {
        const asset = await uploadAsset(pid, file)
        const vp = view.document?.viewport ?? { scale: 1, x: 0, y: 0 }
        designController.addElement(asset.id, (window.innerWidth / 2 - vp.x) / vp.scale, (window.innerHeight / 2 - vp.y) / vp.scale)
      } catch (error) {
        window.alert(String(error))
      }
    }
  }, [pid, view.document?.viewport])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z' && !e.shiftKey) {
        e.preventDefault()
        designController.undo()
      } else if ((e.metaKey || e.ctrlKey) && (e.key.toLowerCase() === 'y' || (e.shiftKey && e.key.toLowerCase() === 'z'))) {
        e.preventDefault()
        designController.redo()
      } else if ((e.key === 'Delete' || e.key === 'Backspace') && view.selectedIds.length > 0) {
        const tag = (e.target as HTMLElement).tagName

        if (tag !== 'INPUT' && tag !== 'TEXTAREA') {
          e.preventDefault()
          designController.removeSelected()
        }
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [view.selectedIds])

  if (view.status !== 'ready' || !view.document) {
    return <div className="grid place-items-center text-sm opacity-60">{view.error ?? t('loading')}</div>
  }

  return (
    <div className="flex h-full min-h-0">
      <div className="relative min-w-0 flex-1">
        <DesignCanvas pid={pid} selectedIds={view.selectedIds} viewport={view.document.viewport} />
        {/* 顶部悬浮工具条 */}
        <div className="absolute left-3 top-3 z-10 flex items-center gap-1 rounded-xl border border-(--ui-stroke-secondary) bg-(--ui-chat-bubble-background)/90 px-2 py-1.5 shadow-lg backdrop-blur">
          <Button onClick={onBack} size="icon-sm" variant="ghost"><Codicon name="chevron-left" /></Button>
          <span className="max-w-40 truncate px-1 text-sm font-medium">{view.document.name}</span>
          <span className="px-1 text-[11px] opacity-50">
            {view.busy ? t('saving') : t('saved')}
          </span>
          <Button onClick={() => designController.undo()} size="icon-sm" variant="ghost" disabled={!designController.canUndo}><Codicon name="discard" /></Button>
          <Button onClick={() => designController.redo()} size="icon-sm" variant="ghost" disabled={!designController.canRedo}><Codicon name="go-to-start" /></Button>
          <Button onClick={() => fileRef.current?.click()} size="icon-sm" variant="ghost"><Codicon name="add" /></Button>
          <Button
            disabled={view.selectedIds.length === 0}
            onClick={() => designController.removeSelected()}
            size="icon-sm"
            variant="ghost"
          >
            <Codicon name="trash" />
          </Button>
          <span className="px-1 text-xs tabular-nums opacity-60">{Math.round((view.document.viewport.scale ?? 1) * 100)}%</span>
          <input
            accept="image/*,video/mp4"
            className="hidden"
            multiple
            onChange={e => void upload(e.target.files)}
            ref={fileRef}
            type="file"
          />
        </div>
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
