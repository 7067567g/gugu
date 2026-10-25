# 06 · Hero flow B（P0）

> 状态：M3 / P0 收口（2026-10-25）。Hero flow 本身在 M1 交付；M2 在其上加了引导层
> （见 `docs/07-guidance.md`）；M3 完成自用 dogfood（自动化路径、图片显式 mock）与收口。
> 契约：`gugu-p0-pipeline/ROUND-SPEC.md`；
> 决策：`gugu-p0-pipeline/DECISIONS.md` D-P0-01 / D-P0-02。
> 代码：`plugins/gugu_capabilities/{diagram,images,export,hero_flow}.py` +
> `registry.py` / `executor.py` / `store.py`。

## 1. 流程

```
repo/README ──► generate_diagram ──► Mermaid(canonical) ──► SVG asset
                                         │ renderer 失败
                                         ├─► structured outline ──► simple SVG
                                         └─► text outline artifact（不硬失败）
prompt ──────► generate_image ──► qwen-image-2.0 ──► gpt-image-2-medium ──► mock*
                                                              （* 非生产 + 显式同意）
artifacts ───► place_on_canvas（标题/标签/坐标）──► 画布
一句修正 ────► update_artifact(instruction) ──► 重生成 ──► 同一 element_id 原地换图
画布 ────────► export_canvas ──► exports/<pid>-r<rev>-<sha8>.zip
```

`hero_flow.py` 把上述步骤串成一次调用，并记录
`time_to_first_artifact_sec / total_sec / human_interventions / element_count / mock_assets /
export.{bytes,sha256,path}`。

## 2. D-P0-01：Mermaid canonical，SVG 落画布

- `generate_diagram(source, source_kind=repo|readme|outline, project_id)`：
  - repo → 顶层目录/关键文件；README → 一/二/三级标题；outline → `A -> B` 或 bullet。
  - **canonical 是 Mermaid 文本**，同时写进 asset 元数据 `diagram.mermaid`。
  - 渲染器是纯 Python 的 **flowchart 子集**（`flowchart/graph TD|TB|LR|RL|BT`、节点
    `[]/()/{}`、`-->/---/==>/-.->/..>`、边标签 `|x|`）。这样 headless 环境不依赖
    `mmdc` / `cairosvg` / Node，输出确定、可 diff、无新依赖。
  - 兜底：renderer 解析失败 → 结构化大纲 → 简单 SVG（`fallback="outline-svg"`）；
    连大纲都不可得 → 文本大纲 artifact（`format="text/markdown"`，`fallback="text"`）。
    绝不返回“看起来成功”的空图。
- **SVG 资产**：`image/svg+xml` 已加入能力侧与 dashboard 侧两套 magic/尺寸校验，扩展名 `.svg`；
  桌面 `fetchAssetUrl` 以 blob 播放，Konva `Image` 直接显示。
- 画布元素新增可选 `label`（`CanvasElement.label`），Konva 在图下画 `Text`；旧文档无该字段
  仍合法。文档标题沿用 `CanvasDocument.name`（工具条 + 导出 preview）。

## 3. D-P0-02：主 / 备 / mock

模型名只来自配置/环境变量，不锁厂商：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `GUGU_IMAGE_MODEL_PRIMARY` | `qwen-image-2.0` | 主模型 |
| `GUGU_IMAGE_MODEL_FALLBACK` | `gpt-image-2-medium` | 备模型 |
| `GUGU_IMAGE_MODELS` | 空 | 额外候选（逗号分隔，排在主后备前） |
| `GUGU_IMAGE_BASE_URL` / `GUGU_IMAGE_API_KEY` | 空 | 显式网关（优先） |
| `GUGU_ALLOW_MOCK_IMAGES=1` | 空 | 非生产允许 mock 兜底 |
| `GUGU_ENV=production`（或 `HERMES_ENV`/`NODE_ENV`） | 空 | 生产禁止 mock |

- 凭据：env 优先；否则**延迟**调用 `hermes_cli.gugu_service.gateway_credentials()` 复用桌面登录。
  凭据只在请求瞬间解析，不写日志/文档/导出。该 X→G 边已在 `arch/baseline.json` 记账（+1，带 reason）。
- 阶梯：请求模型 → 主 → 额外 → 备；每次失败都写进返回的 `attempts`。全部失败且允许 mock →
  `model="mock"`、`mock=true`、文件名 `MOCK-*`、PNG 内有 `MOCK IMAGE / not a real generation` 水印。
  否则 `generation_failed`（带 attempts），**不会**拿旧 asset 冒充新结果。
- 一句修正：`update_artifact(instruction)` 读取当前 asset 的 `prompt`，拼上修正后重新走阶梯；
  成功后只替换同一 `element_id` 的 `assetId/versionId/type`，元素数不变。旧 asset 留在
  `assets` 版本历史里（可追溯），但画布上没有副本。

## 4. 导出

`export_canvas(project_id, format=zip)`（也走 `POST /api/plugins/gugu-design/projects/{pid}/export`，
桌面工具条 `data-agent-id="canvas.export"`）：

```
<pid>-r<rev>-<sha8>.zip
├── project.json      # Host 权威文档
├── preview.svg       # 标题 + 元素图（data URI）+ 标签 + MOCK 标记
├── manifest.json     # 元素/asset/模型/mock/逐资产 sha256
└── assets/*          # 实际字节
```

导出**不改**文档 revision；返回 `path/filename/bytes/sha256/revision/element_count/mock_assets`。

## 5. 端到端证据

```bash
cd /home/a7067567g/下载/deepseek/60-外部仓库/gugu-work
HERMES_HOME=$(mktemp -d) GUGU_ALLOW_MOCK_IMAGES=1 \
  .venv/bin/python -m plugins.gugu_capabilities.hero_flow \
  --repo . --project hero-e2e --out runs/gugu-p0-m1-hero/reports/hero-e2e.json
```

离线/CI 无网关时 3 张配图走显式 mock；有网关时同一命令走主/备模型，其余步骤不变。
M1 实测见 `runs/gugu-p0-m1-hero/reports/hero-e2e.json` 与 `M1-report.md`；
M3 自用 dogfood 见 `runs/gugu-p0-m3-dogfood/reports/dogfood/`（同一命令，图片显式 mock）。

> **不要把 mock 耗时当真实创作耗时**：`time_to_first_artifact_sec` 在 mock 下只证明链路与
> 计时器可用；真实模型延迟、主备切换和失败率需要在有登录凭据的桌面 dogfood 中测。
> `human_interventions` 目前是 `hero_flow.py` 的常量 0，不是现场计数（见
> `docs/09-known-limitations.md`）。

## 6. 明确不做 / 已知缺口

- 引导层在 M2 已交付（`highlight/clear_guidance` 均为 `ready`，见 `docs/07-guidance.md`）；
  hero flow 本身**不强制**显示引导，引导由 Agent 另行调用。
- 不实现 `navigate` 真实路由（`planned`；P0 未交付，列入 backlog）。
- Mermaid 子集渲染器不追求 mermaid.js 全语法；遇到不支持语法会按契约降级，而不是假渲染。
- agent 写入仍不触发 dashboard `/events`（M0 已知），实时可见/session-scoped toolset 留给后续。
- `human_interventions` 是常量 0；owner 真实任务与真实模型延迟尚未实测。
- Windows / Code / 视频 / 故事板不在 P0 范围。
