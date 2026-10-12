/**
 * 无限画布（Konva）—— 手势与渲染。
 * 移植 dsh-design-mode DesignCanvas 的交互模型（指针中心缩放、空格/中键平移、
 * 点选/框选、Transformer 缩放），v1 元素类型 = 图像 + 视频占位卡。
 */
import { Stage, Layer, Image as KonvaImage, Rect, Circle, Text, Group, Transformer } from 'react-konva'
import type Konva from 'konva'
import type { KonvaEventObject } from 'konva/lib/Node'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { fetchAssetUrl } from './api'
import { designController } from './controller'
import type { CanvasElement, CanvasViewport } from './types'

const MIN_SCALE = 0.05
const MAX_SCALE = 8

/** asset → HTMLImageElement 缓存（objectURL 复用）。 */
const imageCache = new Map<string, Promise<HTMLImageElement>>()

function loadElementImage(pid: string, assetId: string): Promise<HTMLImageElement> {
  const key = pid + ':' + assetId
  const hit = imageCache.get(key)

  if (hit) {
    return hit
  }

  const pending = fetchAssetUrl(pid, assetId).then(src => new Promise<HTMLImageElement>((resolve, reject) => {
    const img = new window.Image()

    img.onload = () => resolve(img)
    img.onerror = reject
    img.src = src
  }))

  imageCache.set(key, pending)

  return pending
}

interface ElementImageProps {
  element: CanvasElement
  onActivate: (id: string, additive: boolean) => void
  pid: string
  selected: boolean
  transformerRef: React.RefObject<Konva.Transformer | null>
}

function ElementImage({ element, onActivate, pid, selected, transformerRef }: ElementImageProps) {
  const [img, setImg] = useState<null | HTMLImageElement>(null)
  const nodeRef = useRef<Konva.Group>(null)
  const isVideo = element.type === 'video'

  useEffect(() => {
    if (isVideo) {
      return
    }

    let live = true

    loadElementImage(pid, element.assetId)
      .then(el => {
        if (live) {
          setImg(el)
        }
      })
      .catch(() => undefined)

    return () => {
      live = false
    }
  }, [element.assetId, isVideo, pid])

  // 选中时把 Transformer 绑到本节点
  useEffect(() => {
    const tr = transformerRef.current

    if (tr && selected && nodeRef.current) {
      tr.nodes([nodeRef.current])
      tr.getLayer()?.batchDraw()
    }
  }, [selected, transformerRef])

  const width = element.width
  const height = element.height

  return (
    <Group
      draggable={!element.locked}
      onDragEnd={(e: KonvaEventObject<DragEvent>) => {
        designController.moveElement(element.id, e.target.x(), e.target.y())
      }}
      onTransformEnd={() => {
        const node = nodeRef.current

        if (node) {
          // Group 无固有尺寸：transformer 的 scale 增量折算回 width/height 再复位
          designController.resizeElement(element.id, element.width * node.scaleX(), element.height * node.scaleY())
          node.scaleX(1)
          node.scaleY(1)
        }
      }}
      onMouseDown={(e: KonvaEventObject<MouseEvent>) => {
        onActivate(element.id, e.evt.shiftKey)
      }}
      ref={nodeRef}
      rotation={element.rotation}
      visible={!element.hidden}
      x={element.x}
      y={element.y}
    >
      {isVideo
        ? (
            <>
              <Rect cornerRadius={8} fill="#1f2430" height={height} shadowBlur={12} shadowColor="#000" shadowOpacity={0.35} width={width} />
              <Text
                align="center"
                fill="#cdd3df"
                fontSize={14}
                height={height}
                text={'▶ 视频素材'}
                verticalAlign="middle"
                width={width}
              />
            </>
          )
        : img
          ? <KonvaImage height={height} image={img} width={width} />
          : <Rect cornerRadius={6} fill="#2a2f3a" height={height} width={width} />}
    </Group>
  )
}

export interface DesignCanvasProps {
  pid: string
  selectedIds: readonly string[]
  viewport: CanvasViewport
}

export function DesignCanvas({ pid, selectedIds, viewport }: DesignCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const stageRef = useRef<Konva.Stage>(null)
  const transformerRef = useRef<Konva.Transformer>(null)
  const [size, setSize] = useState({ h: 600, w: 900 })
  const [view, setView] = useState<CanvasViewport>(viewport)
  const [spaceDown, setSpaceDown] = useState(false)
  const panRef = useRef<null | { sx: number; sy: number; vx: number; vy: number }>(null)

  const doc = designController.view.document
  const elements = useMemo(
    () => Object.values(doc?.elements ?? {}).sort((a, b) => a.z - b.z),
    [doc]
  )

  // 容器尺寸跟随布局
  useEffect(() => {
    const el = containerRef.current

    if (!el) {
      return
    }

    const ro = new ResizeObserver(() => {
      setSize({ h: el.clientHeight, w: el.clientWidth })
    })

    ro.observe(el)

    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    setView(viewport)
  }, [viewport])

  // 空格 = 临时 hand 工具
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === 'Space' && !e.repeat) {
        setSpaceDown(true)
      }
    }
    const up = (e: KeyboardEvent) => {
      if (e.code === 'Space') {
        setSpaceDown(false)
      }
    }

    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)

    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  }, [])

  const commitView = useCallback((next: CanvasViewport) => {
    setView(next)
    designController.setViewport(next.scale, next.x, next.y)
  }, [])

  // 指针中心滚轮缩放
  const onWheel = useCallback((e: KonvaEventObject<WheelEvent>) => {
    e.evt.preventDefault()
    const stage = stageRef.current
    const container = containerRef.current

    if (!stage || !container) {
      return
    }

    const pointer = stage.getPointerPosition()

    if (!pointer) {
      return
    }

    const oldScale = view.scale
    const direction = e.evt.deltaY > 0 ? -1 : 1
    const factor = 1.06 ** direction
    const nextScale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, oldScale * factor))
    const mousePointTo = { x: (pointer.x - view.x) / oldScale, y: (pointer.y - view.y) / oldScale }
    const rect = container.getBoundingClientRect()
    const center = { x: rect.width / 2, y: rect.height / 2 }

    commitView({
      scale: nextScale,
      x: center.x - mousePointTo.x * nextScale,
      y: center.y - mousePointTo.y * nextScale
    })
  }, [commitView, view])

  // 平移：中键 / 空格+左键 / hand 工具
  const onPointerDown = useCallback((e: KonvaEventObject<PointerEvent | MouseEvent>) => {
    const evt = e.evt as MouseEvent
    const isPan = evt.button === 1 || (spaceDown && evt.button === 0) || designController.view.tool === 'hand'

    if (!isPan) {
      return
    }

    const pointer = stageRef.current?.getPointerPosition()

    if (pointer) {
      panRef.current = { sx: pointer.x, sy: pointer.y, vx: view.x, vy: view.y }
    }
  }, [spaceDown, view])

  const onPointerMove = useCallback(() => {
    const pan = panRef.current
    const pointer = stageRef.current?.getPointerPosition()

    if (!pan || !pointer) {
      return
    }

    commitView({ scale: view.scale, x: pan.vx + (pointer.x - pan.sx), y: pan.vy + (pointer.y - pan.sy) })
  }, [commitView, view])

  const onPointerUp = useCallback(() => {
    panRef.current = null
  }, [])

  const onActivate = useCallback((id: string, additive: boolean) => {
    const cur = designController.view.selectedIds

    if (additive) {
      designController.select(cur.includes(id) ? cur.filter(x => x !== id) : [...cur, id])
    } else if (!cur.includes(id)) {
      designController.select([id])
    }
  }, [])

  const onStageMouseDown = useCallback((e: KonvaEventObject<MouseEvent>) => {
    // 空白处点击 → 清空选择
    if (e.target === e.target.getStage()) {
      designController.select([])
    }
  }, [])

  return (
    <div
      className="absolute inset-0 overflow-hidden"
      ref={containerRef}
      style={{ cursor: spaceDown ? 'grab' : 'default', touchAction: 'none' }}
    >
      <Stage
        height={size.h}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onMouseDown={onStageMouseDown}
        onWheel={onWheel}
        ref={stageRef}
        scaleX={view.scale}
        scaleY={view.scale}
        width={size.w}
        x={view.x}
        y={view.y}
      >
        <Layer>
          {/* 点阵背景（画布方向感） */}
          <GridLayer size={size} view={view} />
          {elements.map(el => (
            <ElementImage
              element={el}
              key={el.id}
              onActivate={onActivate}
              pid={pid}
              selected={selectedIds.includes(el.id)}
              transformerRef={transformerRef}
            />
          ))}
          <KonvaTransformer ref={transformerRef} selectedIds={selectedIds} view={view} />
        </Layer>
      </Stage>
    </div>
  )
}

/** Transformer 只在有选中时挂上，锚点尺寸随 stage 缩放补偿。 */
function KonvaTransformer({
  ref,
  selectedIds,
  view
}: {
  ref: React.RefObject<Konva.Transformer | null>
  selectedIds: readonly string[]
  view: CanvasViewport
}) {
  if (selectedIds.length === 0) {
    return null
  }

  return (
    <Transformer
      anchorSize={8 / view.scale}
      borderStroke="#4f8bff"
      keepRatio={false}
      ref={ref}
      rotateEnabled={false}
    />
  )
}

function GridLayer({ size, view }: { size: { h: number; w: number }; view: CanvasViewport }) {
  const gap = 48
  const lines: React.ReactNode[] = []
  const x0 = Math.floor(-view.x / view.scale / gap) * gap
  const y0 = Math.floor(-view.y / view.scale / gap) * gap
  const x1 = x0 + size.w / view.scale + gap * 2
  const y1 = y0 + size.h / view.scale + gap * 2

  if ((x1 - x0) / gap > 400 || (y1 - y0) / gap > 400) {
    return null
  }

  for (let x = x0; x < x1; x += gap) {
    for (let y = y0; y < y1; y += gap) {
      lines.push(<Circle fill="#8891a522" key={`${x}:${y}`} radius={1.2 / view.scale} x={x} y={y} />)
    }
  }

  return <>{lines.slice(0, 4000)}</>
}
