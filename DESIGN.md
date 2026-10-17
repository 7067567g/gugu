# DESIGN — gugu-design 规则单源

> 写者：gugu-work 维护者 ｜ 机器校验：`scripts/ci/check_design_rules.py`
> 本文件是 Design 画布 / 生成 / 编排的**唯一规则事实源**；代码、测试与文档只引用本文件，不再复制限额数值。
> 本文件表格中的数值是 `plugins/gugu-design/dashboard/gugu_storage.py` 常量的镜像；两边是否一致由 CI 校验，常量是唯一可执行的权威。

## 1. 画布边界

- **无限画布**：文档只持久化视口 `viewport = {x, y, scale}`，不存画布宽高；平移/缩放只改视口，不改元素坐标。
- **坐标与层级**：元素坐标是画布绝对坐标，`z` 决定绘制顺序；隐藏（`hidden`）与锁定（`locked`）是元素级状态，不删除数据。
- **Host 单一权威**：浏览器不存文档、不直连生成 provider；所有变更经后端串行化后落盘。
- **每工程串行**：per-project 锁把「读-改-写」串成原子操作；同工程多 job 可并发，但文档写不交叉。
- **原子持久化**：`project.json` 用 temp + rename 原子写；`journal.ndjson` 只追加审计，不参与回放决策。
- **乐观并发**：save 必须带 `expected_revision`，冲突返回 409 与最新副本；带 `operation_id` 的请求在幂等窗口内重放同结果。
- **注释锚点**：坐标是相对元素的归一化值（0~1），元素移动/缩放后锚点仍指原处。

## 2. 元素类型

画布元素（`CanvasElement`）只有两种呈现类型，均指向一个资产版本：

| 类型 | 说明 |
| ---- | ---- |
| `image` | 指向 image asset / version；默认按资产原始宽高落位。 |
| `video` | 指向 video asset / version；无原始尺寸时按 16:9 估算高度。 |

元素固定字段：`id`、`assetId`、`versionId`、`type`、`x`、`y`、`width`、`height`、`rotation`、`z`、`hidden`、`locked`。新增类型必须同时更新本表、`CanvasElement` 类型与后端文档校验。

## 3. 资产上限

| 常量 | 文档值 | 约束 |
| ---- | ------ | ---- |
| `MAX_IMAGE_BYTES` | 32 MB | 单个图片资产的字节上限 |
| `MAX_DOCUMENT_BYTES` | 8 MB | 单个 `project.json` 序列化后的字节上限 |
| `OPERATION_HISTORY` | 100 | 幂等窗口保留的最近 operation 数 |

- MIME 以文件头 magic 嗅探为准，不信扩展名与客户端声明；白名单为 png / jpeg / webp / mp4。
- 图片解码后的边长上限由 `MAX_IMAGE_DIMENSION` 控制（本表不重复写数值）。
- 超限一律拒绝入库并返回可读错误，不写半截资产。

## 4. job 状态机

```text
queued → running → succeeded
                 ↘ failed
```

- **提交即排队**：`submit_job` 校验 kind 与请求体，写入 `queued` 后交给后台线程。
- **执行置 running**：worker 拿到网关凭据后开始生成；凭据缺失直接走失败分支。
- **成功**：注册资产、写 `outputAssetIds`，状态置 `succeeded` 并广播事件。
- **失败**：状态置 `failed` 并写 `error.message`（见 §5），产物丢弃。
- **崩溃恢复**：进程重启后首次触碰工程时，`running` / `queued` 一律回到 `queued` 重跑；已付费的网关任务不自动重试。
- 状态只允许沿上图的边迁移；任何写状态的地方都必须持 per-project 锁。

## 5. 失败可见

- 每个失败 job 必须带 `error = {message, retryable}`；`message` 是面向用户的原因，截断到 400 字符。
- 失败唯一写入点是 `_fail_job`：网关未登录、HTTP 非 200、解码失败、未返回图像都必须落成 `failed`，不得静默丢弃。
- 实时可见：`/events` WebSocket 推送 `{"type": "job", "id": ..., "status": "failed"}`；前端另有轮询兜底。
- 错误信息只写原因，不带 token、api key 或 base_url；重试由用户显式发起。

## 6. 编排入口

- **UI 入口**：桌面端 kanban 看板 `board.tsx` 引入并渲染 `OrchestrationPanel`（实现在 `orchestration.tsx`），由设置面板打开。
- **数据面**：`GET /orchestration` 返回 `orchestrator_profile`、`default_assignee`、`auto_decompose`、`auto_promote_children` 以及解析后的 `resolved_*` / `active_profile`；`PUT /orchestration` 只写显式传入的字段，空 profile 字符串表示清除覆盖。
- **前端 API**：`api.ts` 提供 `fetchOrchestration`（GET）与 `saveOrchestration`（PUT），`orchestration.tsx` 只经这两个封装读写。
- **解析规则**：显式 profile 为空或不存在时回落到 active profile；explicit 与 resolved 两组字段同时返回，前端不得自行推断。
