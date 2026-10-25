/**
 * 无限画布（Konva）—— 移植自 dsh-design-mode DesignCanvas.tsx（MIT © DeepSeek），
 * 去 cordis 化：controller 调 gugu 版、类型走本地 ./types。
 *
 * 两条勿丢的不变量（dsh 设计精髓，注释在源码对应处）：
 * 1. **注释/手势坐标全部归一化**（0~1，相对元素自身）——元素移动缩放后锚点仍准确；
 * 2. **Delete = hideSelected（隐藏）**，渲染层过滤 hidden，版本与资产永不因手滑丢失。
 */
import { Circle, Group, Image as KonvaImage, Layer, Rect, Stage, Text, Transformer } from 'react-konva'
import type Konva from 'konva'
import type { KonvaEventObject } from 'konva/lib/Node.js'
import { useEffect, useRef, useState } from 'react'

import { designController } from './controller'
import type { CanvasComment, CanvasDocument, CanvasElement, CanvasTool, CanvasViewport } from './types'

const MIN_SCALE = 0.05
const MAX_SCALE = 4

export interface PendingCommentAnchor {
  kind: 'point' | 'rect'
  versionId: string
  x: number
  y: number
  width?: number
  height?: number
}

type Gesture = { kind: 'rect'; start: Point; current: Point; versionId: string }

interface Point { x: number; y: number }

export interface DesignCanvasProps {
  document: CanvasDocument
  height: number
  onComment: (anchor: PendingCommentAnchor) => void
  selectedIds: readonly string[]
  tool: CanvasTool
  width: number
}

export function DesignCanvas({ document, height, onComment, selectedIds, tool, width }: DesignCanvasProps) {
  const stageRef = useRef<Konva.Stage>(null)
  const [spaceDown, setSpaceDown] = useState(false)
  const [gesture, setGesture] = useState<Gesture | null>(null)
  const viewport = document.viewport

  // 空格 = 临时 hand（dsh 同款）；Ctrl/Cmd+Z undo/redo；Delete 隐藏。
  useEffect(() => {
    const isTyping = (target: EventTarget | null): boolean =>
      target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement

    const onKeyDown = (event: KeyboardEvent): void => {
      if (isTyping(event.target)) {
        return
      }

      if (event.code === 'Space') {
        event.preventDefault()
        setSpaceDown(true)
      }

      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        if (event.shiftKey) {
          designController.redo()
        } else {
          designController.undo()
        }
      }

      if ((event.key === 'Backspace' || event.key === 'Delete') && selectedIds.length > 0) {
        event.preventDefault()
        designController.hideSelected()
      }
    }
    const onKeyUp = (event: KeyboardEvent): void => {
      if (event.code === 'Space') {
        setSpaceDown(false)
      }
    }

    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)

    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
    }
  }, [selectedIds.length])

  const elements = Object.values(document.elements).filter(e => !e.hidden).sort((a, b) => a.z - b.z)
  const elementByVersion = new Map(elements.map(e => [e.versionId, e]))
  // 注释按创建时间编号（1 起）——画布徽章与后续会话列表共用同一序号语义。
  const commentNumbers = new Map(
    Object.values(document.comments)
      .sort((l, r) => l.createdAt - r.createdAt)
      .map((c, i) => [c.id, i + 1])
  )

  const finishGesture = (): void => {
    if (gesture === null) {
      return
    }

    const rect = normalizedRect(gesture.start, gesture.current)

    if (rect.width > 0.005 && rect.height > 0.005) {
      onComment({ kind: 'rect', versionId: gesture.versionId, ...rect })
    }

    setGesture(null)
  }

  return (
    <Stage
      height={height}
      ref={stageRef}
      scaleX={viewport.scale}
      scaleY={viewport.scale}
      width={width}
      x={viewport.x}
      // hand 工具或按住空格 → 整 Stage 可拖（拖完落到 viewport）。
      draggable={tool === 'hand' || spaceDown}
      onDragEnd={(event: KonvaEventObject<DragEvent>) => {
        if (event.target !== event.currentTarget) {
          return
        }

        designController.updateViewport({ scale: viewport.scale, x: event.target.x(), y: event.target.y() })
      }}
      onPointerDown={event => {
        // 空白处按下 = 清空选择（仅 select 工具，注释工具不误触）。
        if (event.target === event.currentTarget && tool === 'select') {
          designController.clearSelection()
        }
      }}
      onPointerUp={finishGesture}
      onWheel={event => {
        event.evt.preventDefault()
        const stage = stageRef.current

        if (!stage) {
          return
        }

        const pointer = stage.getPointerPosition()

        if (!pointer) {
          return
        }

        // 指针中心缩放：先换算指针下的文档坐标，缩放后反推视口原点。
        const oldScale = viewport.scale
        const point = { x: (pointer.x - viewport.x) / oldScale, y: (pointer.y - viewport.y) / oldScale }
        const direction = event.evt.deltaY > 0 ? -1 : 1
        const nextScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, oldScale * (direction > 0 ? 1.08 : 1 / 1.08)))
        designController.updateViewport({
          scale: nextScale,
          x: pointer.x - point.x * nextScale,
          y: pointer.y - point.y * nextScale
        })
      }}
    >
      <Layer>
        {elements.map(element => {
          const asset = document.assets[element.assetId]

          if (!asset) {
            return null
          }

          return (
            <CanvasImage
              element={element}
              key={element.id}
              mediaKind={asset.kind}
              onCommentPoint={(versionId, p) => onComment({ kind: 'point', versionId, ...p })}
              onGestureMove={(versionId, p) =>
                setGesture(cur => (cur && cur.versionId === versionId ? { ...cur, current: p } : cur))}
              onGestureStart={(versionId, p) => setGesture({ current: p, kind: 'rect', start: p, versionId })}
              selected={selectedIds.includes(element.id)}
              tool={tool}
            />
          )
        })}
        {elements.flatMap(element => Object.values(document.comments)
          .filter(c => c.parentVersionId === element.versionId)
          .map(c => (
            <CommentMarker
              comment={c}
              element={element}
              key={c.id}
              number={commentNumbers.get(c.id) ?? 1}
            />
          )))}
        {gesture ? <GesturePreview gesture={gesture} element={elementByVersion.get(gesture.versionId)} /> : null}
      </Layer>
    </Stage>
  )
}

interface CanvasImageProps {
  element: CanvasElement
  mediaKind: 'image' | 'video'
  onCommentPoint: (versionId: string, point: Point) => void
  onGestureMove: (versionId: string, point: Point) => void
  onGestureStart: (versionId: string, point: Point) => void
  selected: boolean
  tool: CanvasTool
}

function CanvasImage({ element, mediaKind, onCommentPoint, onGestureMove, onGestureStart, selected, tool }: CanvasImageProps) {
  const [img, setImg] = useState<null | HTMLImageElement>(null)
  const imageRef = useRef<Konva.Image>(null)
  const transformerRef = useRef<Konva.Transformer>(null)

  // 视频 v1 渲染占位卡（真实缩略帧/播放按钮在 M4 资产面板一并做）。
  useEffect(() => {
    if (mediaKind === 'video') {
      return
    }

    let alive = true

    designController.assetUrl(element.assetId)
      .then(src => {
        const loaded = new window.Image()

        loaded.onload = () => {
          if (alive) {
            setImg(loaded)
          }
        }
        loaded.src = src
      })
      .catch(() => undefined)

    return () => {
      alive = false
    }
  }, [element.assetId, mediaKind])

  // Transformer 绑定选中节点（dsh：单点绑定，多选留 M4）。
  useEffect(() => {
    const tr = transformerRef.current

    if (tr) {
      tr.nodes(selected && imageRef.current ? [imageRef.current] : [])
      tr.getLayer()?.batchDraw()
    }
  }, [selected])

  /** 指针位置 → 元素内归一化坐标（0~1）。所有锚点走这里，保证缩放不变性。 */
  const localPoint = (): Point | null => {
    const point = imageRef.current?.getRelativePointerPosition()

    if (point === null || point === undefined) {
      return null
    }

    return { x: clamp01(point.x / element.width), y: clamp01(point.y / element.height) }
  }

  const pointerDown = (event: KonvaEventObject<PointerEvent | MouseEvent | TouchEvent>): void => {
    const point = localPoint()

    if (point === null) {
      return
    }

    if (tool === 'select') {
      designController.select(element.id, event.evt.metaKey || event.evt.ctrlKey || event.evt.shiftKey)
      return
    }

    if (tool === 'point-comment') {
      onCommentPoint(element.versionId, point)
      return
    }

    if (tool === 'rect-comment') {
      onGestureStart(element.versionId, point)
    }
  }

  return (
    <Group>
      {mediaKind === 'video'
        ? (
            <>
              <Rect
                cornerRadius={8}
                fill="#1f2430"
                height={element.height}
                listening={false}
                rotation={element.rotation}
                shadowBlur={12}
                shadowColor="#000"
                shadowOpacity={0.35}
                stroke={selected ? '#5b8cff' : 'rgba(120,130,150,.25)'}
                strokeWidth={selected ? 2 : 1}
                width={element.width}
                x={element.x}
                y={element.y}
              />
              <Text
                align="center"
                fill="#cdd3df"
                fontSize={14}
                height={element.height}
                listening={false}
                rotation={element.rotation}
                text="▶ 视频素材"
                verticalAlign="middle"
                width={element.width}
                x={element.x}
                y={element.y}
              />
            </>
          )
        : (
            <KonvaImage
              draggable={tool === 'select' && !element.locked}
              height={element.height}
              image={img ?? undefined}
              opacity={img ? 1 : 0.35}
              ref={imageRef}
              rotation={element.rotation}
              shadowBlur={selected ? 14 : 6}
              shadowColor="black"
              shadowOpacity={selected ? 0.24 : 0.12}
              stroke={selected ? '#5b8cff' : 'rgba(120,130,150,.25)'}
              strokeWidth={selected ? 2 : 1}
              width={element.width}
              x={element.x}
              y={element.y}
              onDragEnd={(e: KonvaEventObject<DragEvent>) => {
                designController.updateElement(element.id, { x: e.target.x(), y: e.target.y() })
              }}
              onPointerDown={pointerDown}
              onPointerMove={() => {
                const point = localPoint()

                if (point !== null) {
                  onGestureMove(element.versionId, point)
                }
              }}
              onTransformEnd={() => {
                const node = imageRef.current

                if (!node) {
                  return
                }

                // 四角锚点缩放：scale 增量折算回 width/height 再复位（dsh 同款）。
                const w = Math.max(24, node.width() * node.scaleX())
                const h = Math.max(24, node.height() * node.scaleY())
                node.scaleX(1)
                node.scaleY(1)
                designController.updateElement(element.id, { height: h, width: w, x: node.x(), y: node.y() })
              }}
            />
          )}
      {element.label
        ? (
            <Text
              align="center"
              fill="#8b93a1"
              fontSize={13}
              listening={false}
              rotation={element.rotation}
              text={element.label}
              width={element.width}
              x={element.x}
              y={element.y + element.height + 6}
            />
          )
        : null}
      <Transformer
        anchorFill="#fff"
        anchorSize={9}
        borderStroke="#5b8cff"
        enabledAnchors={['top-left', 'top-right', 'bottom-left', 'bottom-right']}
        keepRatio
        ref={transformerRef}
        rotateEnabled={!element.locked}
        boundBoxFunc={(oldBox, nextBox) => (nextBox.width < 24 || nextBox.height < 24 ? oldBox : nextBox)}
      />
      {element.locked
        ? <Text fontSize={16} listening={false} rotation={element.rotation} text="🔒" x={element.x + 8} y={element.y + 8} />
        : null}
    </Group>
  )
}

/** 注释编号徽章：状态色（draft/open 蓝、running 橙、resolved 绿、failed 红）。 */
function CommentMarker({ comment, element, number }: { comment: CanvasComment; element: CanvasElement; number: number }) {
  const color = comment.status === 'resolved' ? '#3bb273'
    : comment.status === 'failed' ? '#e25555'
      : comment.status === 'running' ? '#f2a93b' : '#5b8cff'

  if (comment.anchor.kind === 'rect') {
    const { x, y, width, height } = comment.anchor
    return (
      <Group listening={false} rotation={element.rotation} x={element.x} y={element.y}>
        <Rect
          dash={[8, 5]}
          fill="rgba(91,140,255,.10)"
          height={height * element.height}
          stroke={color}
          strokeWidth={2}
          width={width * element.width}
          x={x * element.width}
          y={y * element.height}
        />
        <MarkerBadge color={color} number={number} x={(x + width) * element.width} y={(y + height) * element.height} />
      </Group>
    )
  }

  const { x, y } = comment.anchor
  return (
    <Group listening={false} rotation={element.rotation} x={element.x} y={element.y}>
      <MarkerBadge color={color} number={number} x={x * element.width} y={y * element.height} />
    </Group>
  )
}

function MarkerBadge({ color, number, x, y }: { color: string; number: number; x: number; y: number }) {
  const radius = 12
  return (
    <Group listening={false} x={x} y={y}>
      <Circle fill={color} radius={radius} shadowBlur={4} shadowColor="black" shadowOpacity={0.2} stroke={color} />
      <Text
        align="center"
        fill="white"
        fontSize={10}
        fontStyle="bold"
        height={radius * 2}
        offsetX={radius}
        offsetY={6}
        text={String(number)}
        width={radius * 2}
      />
    </Group>
  )
}

function GesturePreview({ element, gesture }: { element: CanvasElement | undefined; gesture: Gesture }) {
  if (!element) {
    return null
  }

  const rect = normalizedRect(gesture.start, gesture.current)
  return (
    <Group listening={false} rotation={element.rotation} x={element.x} y={element.y}>
      <Rect
        dash={[8, 5]}
        fill="rgba(91,140,255,.12)"
        height={rect.height * element.height}
        stroke="#5b8cff"
        strokeWidth={2}
        width={rect.width * element.width}
        x={rect.x * element.width}
        y={rect.y * element.height}
      />
    </Group>
  )
}

function normalizedRect(start: Point, current: Point): { height: number; width: number; x: number; y: number } {
  return {
    x: Math.min(start.x, current.x),
    y: Math.min(start.y, current.y),
    width: Math.abs(current.x - start.x),
    height: Math.abs(current.y - start.y)
  }
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value))
}

export type { CanvasViewport }
