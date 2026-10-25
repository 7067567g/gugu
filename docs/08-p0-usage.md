# 08 · P0 使用说明（gugu 0.9.0）

> 适用：`GUGU_VERSION=0.9.0` 的 P0（agent-native）。范围是“一个 AI 输入框 + Agent 掌控
> 软件 + 画布共享上下文 + 零残留引导”，**不包含** Code 深度、视频/故事板、Windows 产线、
> 协作、插件市场。
> 能力契约见 [`docs/05-agent-capabilities.md`](05-agent-capabilities.md)；已知限制见
> [`docs/09-known-limitations.md`](09-known-limitations.md)。

## 1. 安装与启动

```bash
git clone https://github.com/joydove/gugu.git && cd gugu
uv sync                          # Python 内核
npm ci --legacy-peer-deps        # 桌面端依赖
cd apps/desktop && npm run dev   # 开发模式
```

模型接入二选一：桌面端「网页授权登录」，或手工配置任意 OpenAI 兼容端点
（`docs/03-getting-started.md` §2）。P0 不绑定厂商；图片模型名走
`GUGU_IMAGE_MODEL_PRIMARY` / `GUGU_IMAGE_MODEL_FALLBACK` / `GUGU_IMAGE_MODELS`。

## 2. 找到 AI 输入框（≤1 次点击/快捷键）

- 全局快捷键：**⌘⌥A / Ctrl+Alt+A**。
- 命令面板 **⌘K** → 「打开 AI 输入框（⌘⌥A）」。
- 它复用现有 composer，不新造聊天框：
  - `/design` 已打开工程 → 聚焦生成面板 textarea（`design.composer`）；
  - `/design` 工程列表 → 回 `/` 再聚焦核心 Chat composer；
  - 其它界面 → 直接聚焦核心 Chat composer。

## 3. Agent 能做什么 / 现在是什么状态

对 Agent 说“你能做什么 / 现在是什么状态”，它会调用单工具 `gugu_capability`：

```text
gugu_capability(capability="list_capabilities")   # 11 个能力 + 可指锚点
gugu_capability(capability="read_state",
                arguments={"scope":["surface","project","canvas","selection"]})
```

- `list_capabilities` 返回 `id / 语义 / input_schema / output_schema / side_effects /
  requires_approval / ui_anchors / examples / status / milestone`，以及 `anchors`
  （`targetable=true` 才能被高亮）。
- `read_state` 返回四个键；`project_id` 显式传入优先，否则读渲染端发布的
  `designs/agent-state.json`。
- `planned` 能力（当前只有 `navigate`）返回 `not_implemented`，不会伪造成功。

## 4. Hero flow B（P0 唯一验收流程）

1. 在任意界面按 ⌘⌥A，说：“把这个 repo 变成一张架构图 + 3 张配图。”
2. Agent 读 README/目录 → 最多问 1 个澄清（风格/比例）→ 调 `generate_diagram`
   （Mermaid canonical → SVG）与 3 次 `generate_image`。
3. 产物按标题/标签/坐标落到画布（`place_on_canvas`，同 key 幂等）。
4. Agent 用 `highlight` 指画布/导出按钮；ESC、点击空白、路由/模式切换、Agent 取消、
   组件卸载、异常都会结束引导且零残留。
5. 说：“第二张太冷，暖一点。” → `update_artifact(instruction)` 只替换同一
   `element_id`，不新增画布副本。
6. 点工具条导出（锚点 `canvas.export`）或让 Agent 调 `export_canvas` → 自包含 zip：
   `project.json` + `preview.svg` + `manifest.json`（逐资产 sha256）+ `assets/`。

### 无桌面的最小验证

```bash
cd gugu-work
HERMES_HOME=$(mktemp -d) GUGU_ALLOW_MOCK_IMAGES=1 \
  .venv/bin/python -m plugins.gugu_capabilities.hero_flow \
  --repo . --project p0-demo --out /tmp/p0-hero.json
```

无网关凭据时 3 张配图走**显式 mock**（`mock=true`、`MOCK-*` 文件名、PNG 水印）；
`time_to_first_artifact_sec` 此时只证明链路，不代表真实模型延迟。

## 5. 引导（highlight / clear_guidance）

```python
executor.execute("highlight", {"target_anchor": "canvas.export", "text": "导出在这里"})
executor.execute("highlight", {"target_anchor": "design.canvas", "style": "box", "timeout_ms": 8000})
executor.execute("clear_guidance", {})     # 幂等
```

- `style`：`highlight`（实线框 + 光晕）/ `box`（虚线框）/ `label`（仅说明气泡）。
- 只指 `list_capabilities.anchors` 里 `targetable=true` 的锚点；`guidance.overlay` 不可自指。
- 结束/中断后：overlay=0、target 标记=0、listener=0、timer=0，焦点/滚动不被抢，布局不变。
- 传输是 `designs/guidance.json` 邮箱 + `/design` 约 1.2s 轮询：**不是实时**，且需要
  `/design` 已挂载。详见 [`docs/07-guidance.md`](07-guidance.md)。

## 6. 导出

- 桌面：画布工具条 `canvas.export`。
- HTTP：`POST /api/plugins/gugu-design/projects/{pid}/export`。
- 能力：`export_canvas(project_id)`。
- 导出**不改**文档 revision；返回 `path/filename/bytes/sha256/revision/element_count/mock_assets`。
- mock 资产在 preview/manifest 里保留显式标记，不会伪装成真实生成。

## 7. 常用环境变量

| 变量 | 作用 |
| --- | --- |
| `GUGU_IMAGE_MODEL_PRIMARY` / `_FALLBACK` / `GUGU_IMAGE_MODELS` | 图片模型阶梯（不锁厂商） |
| `GUGU_IMAGE_BASE_URL` / `GUGU_IMAGE_API_KEY` | 显式图片网关（优先于桌面登录） |
| `GUGU_ALLOW_MOCK_IMAGES=1` | 非生产允许显式 mock 兜底 |
| `GUGU_ENV=production`（或 `HERMES_ENV`/`NODE_ENV`） | 生产禁止 mock |
| `HERMES_HOME` | 设计工程/资产根（默认 `~/.hermes`） |

凭据只走环境变量或桌面登录，不写日志/文档/导出；仓库内不得提交 API key。

## 8. 验收 / 门禁命令

```bash
cd gugu-work
.venv/bin/python -m pytest tests/plugins/test_gugu_capabilities.py \
  tests/plugins/test_gugu_capabilities_m1.py tests/plugins/test_gugu_agent_state_api.py \
  tests/plugins/test_gugu_design_slice.py tests/plugins/test_gugu_guidance.py \
  tests/plugins/test_gugu_p0_docs_contract.py -q --timeout=600
.venv/bin/python -m pytest tests/ci -q --timeout=600
.venv/bin/python scripts/arch/check_boundaries.py --baseline arch/baseline.json
cd apps/desktop && npm run typecheck && npx vitest run --project ui src/plugins/gugu-design
```

## 9. 故障排查

| 现象 | 先看 |
| --- | --- |
| `approval_required` | `generate_diagram` / `generate_image` 需要 UI/E2E 侧 `approved=True`；Agent 工具不暴露该参数 |
| `not_implemented` | 该能力仍是 `planned`（当前只有 `navigate`） |
| `generation_failed` | 主/备模型都失败且未允许 mock；看返回的 `attempts` 与网关配置 |
| `project_not_found` | `project_id` 显式传错；工程列表里重新选 |
| 引导不出现 | `/design` 是否挂载；邮箱 `designs/guidance.json`；约 1.2s 轮询延迟 |
| 画布看不到 Agent 改动 | 直写 `project.json` 不触发 `/events`；下次保存/重进工程才会重拉（已知缺口） |
| 导出失败 | 空画布会返回 `export_failed`；先放至少一个元素 |

P0 的完整验收状态与未完成项：`runs/gugu-p0-m3-dogfood/reports/P0-CLOSEOUT.md`。
