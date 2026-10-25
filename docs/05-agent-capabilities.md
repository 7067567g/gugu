# 05 · Agent capabilities（P0 M0 骨架）

> 状态：M1（2026-10-25）。M0 的契约 + 最小读写之上，M1 接入真实生成（Mermaid→SVG /
> 主备模型→显式 mock）、原地修正与导出；引导层仍在 M2。
> 代码事实源：`plugins/gugu_capabilities/registry.py`（能力/锚点）、`executor.py`（执行）、
> `store.py`（画布/资产）、`diagram.py` / `images.py` / `export.py`（M1）。
> 本文不重复 schema 细节，只解释边界与取舍。

## 1. 一个 AI 输入框

- 桌面端全局快捷键 **⌘⌥A / Ctrl+Alt+A**，以及 ⌘K 命令面板里的「打开 AI 输入框（⌘⌥A）」。
- **不新造聊天框**：
  - `/design` 且已打开工程 → 聚焦现有生成面板的 textarea（锚点 `design.composer`）；
  - `/design` 工程列表（没有 composer）→ 先回 `/` 再聚焦核心 Chat composer；
  - 其它界面 → 直接聚焦核心 Chat composer。
- 决策是纯函数 `agent-entry.ts::planAiEntry`，快捷键与命令面板共用；单测覆盖 fallback。
- 这是 M0 的入口；M1 的 hero flow 会在这个输入框里接 plan strip 与能力编排。

## 2. capability registry（11 个）

每个能力在 registry 里都有：`id / semantics / input_schema / output_schema /
side_effects / requires_approval / ui_anchors / examples`，外加 `status` 与 `milestone`。
`status=ready` 才会被执行；`planned` 一律返回 `not_implemented`，**不返回假结果**。

| 能力 | 状态 | 审批 | 本轮行为 |
| --- | --- | --- | --- |
| `navigate(screen)` | planned (M1) | 否 | 契约登记；跨进程导航通道 M1 做 |
| `read_state(scope)` | ready | 否 | 读 surface / project / canvas / selection |
| `list_capabilities()` | ready | 否 | 返回完整 registry |
| `generate_diagram(source)` | ready (M1) | 是 | repo/README/outline → Mermaid canonical → SVG asset；失败 outline SVG → 文本大纲 |
| `generate_image(prompt, refs)` | ready (M1) | 是 | 主 qwen-image-2.0 → 备 gpt-image-2-medium → 非生产显式 mock；模型名走 env |
| `place_on_canvas(artifact, position)` | ready | 否 | 落一个已有 asset；同 key 幂等 |
| `update_artifact(id, …)` | ready | 否 | 原地替换 asset/几何，不新增副本 |
| `highlight(target, text)` | planned (M2) | 否 | 引导层 M2；本轮不渲染 overlay |
| `clear_guidance()` | planned (M2) | 否 | 零残留契约 M2 |
| `undo()` | ready | 否 | 画布快照栈（最多 10 步） |
| `export_canvas(project_id)` | ready (M1) | 否 | 自包含 zip：project.json + assets + preview.svg + manifest 校验和 |

### 执行信封

```json
{"ok": true, "capability": "read_state", "data": {...},
 "meta": {"schema_version": "gugu.capability-registry/v1"}}
```

失败：`{"ok": false, "capability": "...", "error": {"code": "...", "message": "...", "details": {...}}}`。
稳定错误码：`unknown_capability / invalid_arguments / approval_required / not_implemented /
project_required / project_not_found / project_document_missing / unknown_asset / element_not_found /
element_conflict / element_locked / generation_required / generation_failed / export_failed /
invalid_position / internal_error`。

**审批**：`generate_diagram` / `generate_image` 标记 `requires_approval`。未审批一律
`approval_required`；执行器不会把“没实现”或“没凭据”变成假成功。agent 工具**不暴露**
`approved` 参数（模型不能自我审批）；审批入口留给桌面 UI / E2E 调用方。

## 3. Agent 怎么调用

用**现有插件工具机制**（不新增 core tool，也不做第二套 MCP server）：

- plugin：`plugins/gugu_capabilities/`（`plugin.yaml` 声明 `provides_tools: [gugu_capability]`）。
- 单工具 `gugu_capability(capability, arguments)`：先 `list_capabilities` 发现，再按 id 调用。
  一个工具而不是十个，避免每次 API 调用都为 10 份 schema 付费。
- 工具 handler 只做 JSON 信封，所有校验/副作用在 `executor.execute()`。

已知边界（M1）：工具当前随 bundled 插件常驻；远端 backend 上它读写的是**该 backend 的**
`HERMES_HOME/designs`，不是桌面客户端的文件。M1 需要按 session source（`desktop`）收口到
`desktop_ui` 一类的 session-scoped toolset，并改走 host RPC/WS。

## 4. 画布最小读写

存储复用 gugu-design 的工程文档：`<HERMES_HOME>/designs/<pid>/project.json`
（`CanvasDocument` v1）。M0 只动 `elements / assets / versions / viewport` 四个字段。

- **跨进程串行**：dashboard 的 `threading.Lock` 只在本进程有效；capability 在
  `<project>/agent.lock` 上用 `fcntl.flock` 串行 **agent 自己的**读改写，temp+rename 原子写。
  dashboard 的保存**不参与**这把锁，靠 `revision` 乐观锁兜底（agent 写后 dashboard 保存会 409 →
  重拉权威文档）。M1 把 agent 写入收到 host RPC 才能彻底消除这条竞争。
- **放置幂等**：元素 id 由 `element_id` 或 `artifact.key` 的 sha256 派生；同一 key 再放置
  直接返回已有元素（`created:false, idempotent:true`）。显式 `operation_id` 命中
  `agent-ops.json` 时连 revision 都不再 bump。
- **原地替换**：`update_artifact` 只改同一 `element_id` 的 `assetId/versionId/type/几何`，
  元素数量不变；目标状态与现状一致时返回 `idempotent:true` 且不写盘。
  `instruction` 单独出现返回 `generation_required`（真实重生成是 M1）。
- **undo**：每次写前把画布四字段快照进 `agent-undo.json`（上限 10），undo 只合并回画布字段，
  不覆盖 Host 的 `jobs/comments`。
- **活动日志**：`agent-activity.ndjson` 追加 `{ts, capability, revision, detail}`。

### 跨进程可见性（已知缺口）

agent 直写 `project.json` 不会触发 dashboard 的 `/events` WS 广播。M0 的渲染端在下次保存
（409 → 重新拉取）或重新进入工程时看到 Agent 的改动。M1 应把 agent 写入改到 host RPC/WS
（或由 dashboard 监听文件并广播），本条已写入 `VERDICT` 的下一轮建议。

## 5. 状态读取

渲染端把「用户在看什么 / 选了什么」发布到 `designs/agent-state.json`
（`PUT /api/plugins/gugu-design/agent-state`，最后写入者胜，失败不影响画布）。
`read_state` 读同一个文件：

```json
{
  "surface": "design",
  "route": "/design",
  "project_id": "20261025-...",
  "selection": ["agent-..."],
  "updated_at": 1790000000.0
}
```

- `scope` 可裁剪计算范围，但返回信封始终含四个键（未请求/不存在的为 `null` / 空数组）。
- `project_id` 显式传入优先；否则用 runtime state。指向已删除工程时安静降级为 `null`
  （显式传错 id 才报 `project_not_found`）。
- selection 会解析成元素摘要；工程不可读时只返回 id。

## 6. UI 锚点

锚点统一 `data-agent-id="{surface}.{element}"`，在 `registry.py::ANCHORS` 登记
`present|planned`。`ready` 能力只能引用 `present` 锚点（测试强制）。

M0 实际落到桌面的锚点：

`design.projects` / `design.projects.name` / `design.projects.create` /
`design.workspace` / `design.canvas` / `design.canvas.undo` / `design.canvas.redo` /
`design.canvas.fit` / `design.canvas.upload` / `design.canvas.hide` /
`design.panel` / `design.panel.jobs` / `design.composer` / `design.composer.kind` /
`design.composer.send`

`canvas.export` 在 M1 变成 present（画布工具条的导出按钮）；`guidance.overlay` 在 M2
变成 present（引导 active 时挂到 `body`），但 `targetable=false`，不能作为高亮目标。

M2 起 `list_capabilities` 的返回信封多一个 `anchors` 字段，Agent 可直接枚举：

```json
{"id": "canvas.export", "surface": "canvas", "status": "present",
 "description": "画布导出按钮（M1）", "targetable": true}
```

`targetable = status == "present" and surface != "guidance"`。排序稳定（按 id），
`highlight` 的 `unknown_anchor` 错误会带上当前可指锚点列表。详见 `docs/07-guidance.md`。

## 7. 测试与门禁

```bash
# 能力契约 + 真跑（M0 + M1）
.venv/bin/python -m pytest tests/plugins/test_gugu_capabilities.py \
  tests/plugins/test_gugu_capabilities_m1.py -q -x --timeout=600
# agent-state API ↔ read_state 集成（4）
.venv/bin/python -m pytest tests/plugins/test_gugu_agent_state_api.py -q -x --timeout=600
# 引导层：锚点枚举 + 邮箱 + 端点（13）
.venv/bin/python -m pytest tests/plugins/test_gugu_guidance.py -q -x --timeout=600
# 桌面（纯函数 + 零残留 DOM 契约）
cd apps/desktop && npx vitest run --project ui src/plugins/gugu-design
# arch
.venv/bin/python scripts/arch/check_boundaries.py --baseline arch/baseline.json
```

覆盖点：registry meta-schema / 示例参数合法性 / 11 个 id；Mermaid→SVG 与两级兜底；SVG 资产
双校验器一致；主/备/显式 mock 阶梯；instruction 原地换图不增副本；导出 zip；hero flow 计时；`list_capabilities`、`read_state`
真跑；放置幂等（自然 key + operation_id 重放）；原地替换不增副本；undo 多步与 Host 字段保护；
审批与 planned 不伪装；插件工具注册与 handler 执行；M2 锚点枚举、highlight 邮箱、
clear 幂等、7 个中断源的零残留。

## 8. 下一轮接口

- M1 剩余：`navigate` 跨进程通道；agent 写入走 host RPC/WS（实时可见）；session-scoped
  toolset 收口；plan strip。
- M1 已交付：`generate_*` 真实实现 + 审批、原地修正、导出、hero flow E2E（见 `docs/06-hero-flow.md`）。
- M2 已交付：`highlight/clear_guidance` ready；锚点表复用并可由 `list_capabilities.anchors`
  枚举；`/design` 消费引导邮箱；零残留按 7 个中断源自动测试（见 `docs/07-guidance.md`）。
- M2 剩余：host RPC/WS 取代 1.2s 轮询；多步 step 队列；Python↔TS 锚点漂移自动检测。
