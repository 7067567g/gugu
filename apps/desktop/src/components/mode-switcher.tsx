/**
 * 模式切换器 —— 顶栏左簇（侧栏收起按钮旁，用户指定位置）的三档分段控件。
 * 架构见仓库根 docs/02-三模式架构.md §4.2。
 */
import { useStore } from '@nanostores/react'

import { APP_MODE_DEFS, setAppMode } from '@/app/modes'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Code, LayoutDashboard, MessageCircle } from '@/lib/icons'
import { $appMode } from '@/store/app-mode'

const MODE_ICONS = {
  'comment-discussion': MessageCircle,
  code: Code,
  layout: LayoutDashboard
} as const

export function ModeSwitcher({ t }: { t: { modeDesign: string; modeIde: string; modeWork: string } }) {
  const mode = useStore($appMode)

  return (
    <SegmentedControl
      className="h-6 self-center"
      onChange={setAppMode}
      options={APP_MODE_DEFS.map(def => ({ id: def.id, icon: MODE_ICONS[def.icon], label: t[def.labelKey] }))}
      value={mode}
    />
  )
}
