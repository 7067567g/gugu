# 快速上手（Getting Started）

从零把咕咕跑起来：对话 → 出图。全程约 15~30 分钟（取决于网络）。

## 0. 环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | ≥3.11 且 <3.14 | 推荐用 [uv](https://docs.astral.sh/uv/) 管理 |
| Node.js | ≥22.22（推荐 24.x） | 桌面端构建必需，Node 20 会被 engines 拦 |
| Git | 任意新版 | Windows 贡献者建议 Git Bash |
| ffmpeg | 可选 | 故事板拼接成片用 |

## 1. 克隆与安装

```bash
git clone https://github.com/joydove/gugu.git
cd gugu

# Python 内核（生成 .venv/）
uv sync

# 桌面端依赖（npm workspaces 单锁文件）
npm ci --legacy-peer-deps
```

> **为什么 `--legacy-peer-deps`**：`react-konva@19.2` 的 peer 范围与仓库锁定的
> React 19.2.x 组合在 npm 严格解析下会报 ERESOLVE。这是已知限制，CI 同样带此参数。
> **不要用 `npm install`**：对本仓库的 workspace 树会触发 npm 解析 bug（静默丢包），
> 一切以 `npm ci` 为准；新增依赖请先在 lock 里可解析再提 PR。

## 2. 配置模型接入（二选一）

咕咕的模型流量统一走 **OpenAI 兼容聚合网关**（chat / images / videos 全模态）。

### 方式 A：咕咕网页授权（推荐，桌面端）
1. 启动桌面端（见 §3），首次进入登录页选择「咕咕聚合网关」；
2. 浏览器自动打开本机授权页，粘贴网关 API key（乐鸽聚合平台内测中，
   key 通过申请/邀约获取）；
3. 验证通过后 key 写入 `~/.hermes/.env`，配置自动指向网关。

### 方式 B：手工配置任意 OpenAI 兼容端点
编辑 `~/.hermes/config.yaml`：

```yaml
model:
  default: "qwen3.7-max"          # 换成你网关上的聊天模型
  provider: "custom"
  base_url: "https://your-gateway.example.com/v1"
  api_key: "${MY_GATEWAY_KEY}"     # 密钥本体放 ~/.hermes/.env
```

```bash
# ~/.hermes/.env
MY_GATEWAY_KEY=sk-xxx
```

## 3. 跑起来

### CLI（最快验证）
```bash
# Linux/macOS
.venv/bin/hermes -z "用一句话介绍你自己" --cli
# Windows (Git Bash)
.venv/Scripts/hermes.exe -z "用一句话介绍你自己" --cli
```

### 桌面端（开发模式）
```bash
cd apps/desktop
npm run dev          # vite + electron 双进程
```

### 桌面端（绑定本地源码内核）
开发模式的后端解析走环境变量（Windows 示例）：
```bat
set HERMES_HOME=%CD%\.hermes-home
set HERMES_DESKTOP_HERMES=<repo>\.venv\Scripts\hermes.exe
set HERMES_DESKTOP_PYTHON=<repo>\.venv\Scripts\python.exe
```

## 4. 验证清单

- [ ] 对话：随便发一句话，有流式回复
- [ ] 出图：对 Agent 说「生成一张猫穿宇航服的图」→ `image_generate` 工具落盘
- [ ] 出视频：说「生成一段 5 秒的猫在月球散步视频」→ `video_generate` 异步任务
- [ ] Design 模式：顶栏左角「工作/设计/编程」切换器 → 进「咕咕设计」建工程出图

## 5. 常见坑

- **Windows Git Bash 里 PATH 要写 `/d/...` 格式**，`D:\...` 不生效。
- **`npm install` 会污染依赖树**：修复只能 `rm -rf node_modules && npm ci --legacy-peer-deps`。
- **`.gitignore` 的 `.*/` 规则**：新增点目录（`.docs/` 除外）的文件会被静默忽略，
  确需入库请加 `!` 白名单（必须排在 `.*/` 之后）或 `git add -f`。
- **模型列表异常**：网关 `/v1/models` 混返多模态时，内核按 `modality` 字段只收
  chat 模型；自定义网关若不返回 `modality` 字段则全收。

## 6. 下一步

- 架构必读：[docs/02-三模式架构.md](02-三模式架构.md)（改任何模式相关代码前先读）
- 路线图与认领：[docs/04-roadmap.md](04-roadmap.md)
- 贡献流程：[CONTRIBUTING.md](../CONTRIBUTING.md)
