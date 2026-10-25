# 07 · 引导层（M2，P0 收口保留）

> 状态：M3b / P0 收口（2026-10-25）。引导层在 M2 交付；M3b 把消费循环从 `/design`
> 页面提升到 app 级 runtime，任意界面可 highlight/box/label，零残留契约不变。
> 已知限制见 `docs/09-known-limitations.md` §4。
> 契约：`gugu-p0-pipeline/ROUND-SPEC.md` 硬边界 9；
> 产品规范：`strategy/product/gugu-p0-consolidation.md` §4。
> 代码：`apps/desktop/src/plugins/gugu-design/{guidance,global-runtime}.ts` +
> `plugins/gugu_capabilities/{registry,executor,store}.py` +
> `plugins/gugu-design/dashboard/plugin_api.py`。

## 1. 目的与边界

Agent 用高亮 / 图框 / 说明指一下界面上的元素，用户随时可以打断；结束后界面必须像
引导从未发生过。它是**一次性指路**，不是新手关：不做常驻 tour、不做遮罩教程、不做
录屏、不要求用户按顺序点完。

## 2. 生命周期

```text
idle ──show(step)──► active(step) ──┬── done          （超时/正常完成）
                                    ├── cancelled     （用户主动取消）
                                    └── interrupted   （外部原因）
                                              │
                                              └──► idle
```

每个结束都带 `last_end = {guidance_id, reason, interrupt}`。同一时刻只有一个
step；新的 `show()` 先把旧的按 `interrupted/agent` 收尾，再渲染新的（不叠 overlay）。

| 中断源 | 触发机制 | reason / interrupt |
| --- | --- | --- |
| ESC | `document` 捕获阶段 `keydown` | cancelled / escape |
| 点击空白 | `document` 捕获阶段 `pointerdown`，目标不在 overlay 内 | cancelled / blank-click |
| 路由切换 | `window` `hashchange`（manager 自持） | interrupted / route |
| 模式切换 | 生成面板 image↔video 切换调用 `interrupt('mode')` | interrupted / mode |
| Agent 取消 | `clear_guidance` 删邮箱；渲染端轮询发现后 `clear()` | cancelled / agent |
| 组件卸载 | 插件 `onDispose` → app 级 runtime `stop()` → `dispose()`（不再由 `/design` 页面 cleanup 触发） | interrupted / unmount |
| 异常 | overlay 挂载或测量抛错 → 清理后 `interrupted/error`，不抛给 React | interrupted / error |
| 超时 | step 自带 `timeout_ms`（0 = 不超时） | done / timeout |

注意：焦点归用户。ESC / 空白点击 / 路由切换 / 模式切换通常由用户手势驱动，结束时不
抢回焦点——用户点哪里焦点就在哪里；只有 Agent 取消、异常、超时、卸载这类用户没有移动
焦点的结束才恢复引导开始前的焦点与滚动（目标仍 connected 时）。

## 3. 零残留契约

一次引导结束后必须同时满足（`guidance.test.ts` 对每个中断源都断言；
`global-runtime.test.ts` 在 app 级 runtime 上重跑同一组零残留断言）：

- `document.querySelectorAll('[data-gugu-guide]').length === 0`
- `document.querySelectorAll('[data-gugu-guide-target]').length === 0`，且目标元素的
  `style` 属性与引导前一致（manager 从不写目标样式）
- manager 的 listener 计数 = 0（document 2 个 + window 1 个，成对解绑；另有测试用
  `addEventListener` / `removeEventListener` spy 核对调用数）
- timer 计数 = 0（`vi.getTimerCount()` 交叉验证）
- 焦点不因引导被抢；程序性结束时按下文规则复原；页面滚动位置不变（不调用
  `scrollTo` / `scrollBy` / `scrollIntoView`）
- overlay 只挂在 `body`，`position:fixed; pointer-events:none`，不进入目标子树，
  不新增/删除工作区子节点 → 不改变布局
- 订阅在 dispose 时全部断开（`guidanceLayer.dispose()` 幂等）

## 4. 锚点（同一套锚点服务操作与引导）

- DOM 侧统一 `data-agent-id="{surface}.{element}"`；已存在的锚点见
  `docs/05-agent-capabilities.md` §6。overlay 自身在 active 时挂
  `data-agent-id="guidance.overlay"`，但**不可作为高亮目标**（自指会框住自己）。
- `registry.ANCHORS` 是唯一登记表；`list_capabilities` 新增 `anchors` 字段：

```json
{"id": "canvas.export", "surface": "canvas", "status": "present",
 "description": "画布导出按钮（M1）", "targetable": true}
```

`targetable = status == "present" and surface != "guidance"`。Agent 先枚举再 `highlight`，
不会指到不存在的元素；Python 侧对 unknown / planned / 自指锚点分别返回
`unknown_anchor` / `anchor_not_available` / `invalid_anchor`，且不写邮箱。

## 5. 传输：邮箱 + 轮询（已知限制）

M1 遗留的 renderer 方向 host 通道（RPC/WS）尚未建，本轮不硬造。Agent 侧把当前唯一
一步写到 `designs/guidance.json`（原子写，最后写入者胜）：

```json
{"guidance_id": "gd-…", "target_anchor": "design.canvas", "text": "产出会落在这里",
 "style": "highlight|box|label", "timeout_ms": 0, "created_at": 1790000000.0}
```

- `GET /api/plugins/gugu-design/guidance` 由 app 级 runtime 轮询（约 1.2s），只读；
  不依赖 `/design` 页面挂载。
- `DELETE …/guidance?guidance_id=…` 由渲染端在本地中断后 best-effort 调用；带 id
  作用域，迟到的 dismiss 不会删掉 Agent 刚写入的下一步。
- `clear_guidance` 删除邮箱；重复调用返回 `cleared=false`。

代价：引导从 Agent 调用到出现在屏幕上最多约 1.2s（仍是轮询，不是推送）。M3b 去掉了
“必须停留在 `/design`”的限制；真正的 host 通道仍是 backlog（B-04），不把轮询说成实时。

跨 surface 锚点：`work.composer` / `settings.header` / `artifacts.root` 与 `design.*`
共用同一 `ANCHORS` 表；Agent 从 `list_capabilities.anchors` 的 `surface` 字段即可知道
某个锚点在哪个界面，先 `navigate` 再 `highlight`。

## 6. 用法

```python
executor.execute("highlight", {"target_anchor": "canvas.export", "text": "导出在这里"})
executor.execute("highlight", {"target_anchor": "design.canvas", "style": "box", "timeout_ms": 8000})
executor.execute("clear_guidance", {})            # 幂等
```

`style`：`highlight`（实线框 + 光晕）/ `box`（虚线框）/ `label`（仅说明气泡）。
`text` 可为空；为空时 `label` 不画框。`timeout_ms` 上限 60000。

## 7. 测试

```bash
cd apps/desktop && npx vitest run --project ui src/plugins/gugu-design
cd /home/a7067567g/下载/deepseek/60-外部仓库/gugu-work
.venv/bin/python -m pytest tests/plugins/test_gugu_guidance.py -q --timeout=600
```

桌面测试：`guidance.test.ts` 28 条（渲染 3 种 style、重复 show 不叠层、7 个中断源逐个
断言零残留、超时与提前中断清 timer、listener 调用数、焦点/滚动、布局不变、挂载/测量异常、
订阅生命周期）；`global-runtime.test.ts` 13 条在 app 级 runtime 上重跑跨 surface 引导、
路由中断、非当前 surface 锚点 dismiss、`stop()` 后 overlay/target/listener/timer 全零。
Python 测试：registry 契约、锚点枚举（含 work/settings/artifacts）、邮箱写入/清除/作用域、
端点 GET/DELETE、schema 与错误码。

## 8. 非目标 / 下一轮

- 非目标：常驻 tour、遮罩教程、录屏、多步强制流程、引导历史回放。
- 下一轮：host RPC/WS 取代轮询（实时、跨路由）；多步 step 队列；锚点集合的
  Python↔TS 自动漂移检测（当前两侧各自测试，尚无共享清单）；真实 Electron 窗口的
  视觉回归（jsdom 无布局）。
