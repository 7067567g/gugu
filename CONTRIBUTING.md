# 贡献指南（CONTRIBUTING）

欢迎参与咕咕共建。动手之前，请先读完本页——尤其是「文档先行」与「.gitignore 陷阱」两节。

## 1. 开发环境

见 [docs/03-getting-started.md](docs/03-getting-started.md) §0-§3。要点速记：

- Python ≥3.11 <3.14（`uv sync`）；Node ≥22.22（推荐 24）
- **依赖安装只用 `npm ci --legacy-peer-deps`**；`npm install` 对本仓库 workspace 树
  会触发 npm 解析 bug（静默丢包/污染树），修复手段是删 `node_modules` 重装
- Windows + Git Bash：PATH 必须写 `/d/...` 格式，`D:\...` 不生效

## 2. 核心原则：文档先行

**任何模式（Work/Design/Code）相关的改动，先对齐
[docs/02-三模式架构.md](docs/02-三模式架构.md)，改完回写该文档。**

- docs/02 是三模式的权威架构文档：扩展点清单（带 file:line）、决策记录（ADR）、
  「如何加第四种模式」操作手册都在里面
- 你的改动如果与 docs/02 冲突，要么改方案，要么在 PR 里说明为什么该更新文档
- 关键约束写成**代码注释**（写"为什么"，不写"是什么"），防止后续上下文丢失导致误改

## 3. 仓库结构速览

```
hermes_cli/        Python 内核 CLI + dashboard 后端（FastAPI）
agent/ tools/      Agent 运行时与工具
plugins/           Python 插件（含 gugu-design 后端、video_gen/gugu）
apps/desktop/      Electron 壳 + React 渲染层（三模式 UI 都在这里）
  src/plugins/     桌面 bundled 插件（gugu-design 前端）
  src/app/         壳层：路由/窗格/标题栏/模式注册表(modes.ts)
docs/              公开文档（架构、上手、路线图）
LICENSES/          上游许可全文（归属要求，勿删）
```

## 4. .gitignore 陷阱（重要）

本仓库用 `.*/` 通吃规则防止各贡献者本地 Agent/IDE 工具目录（`.claude/`、`.cursor/`、
`.gemini/` 等）混入仓库。**副作用**：任何「新的」点目录里的文件会被 `git add`
静默跳过、`git status` 也不显示。

- 白名单已有：`.github/`、`.devcontainer/`
- 确需新增点目录入库：在 `.gitignore` 末尾加 `!.你的目录/`（**必须排在 `.*/` 之后，
  不要重排顺序**），或对单个文件 `git add -f`
- 内部/私人文档放 `.docs/`（已被忽略），公开文档放 `docs/`

## 5. 提交流程

1. 从 `main` 开分支：`feat/design-canvas`、`fix/...`、`docs/...`
2. 本地验证：`npm run typecheck`（apps/desktop）+ 受影响测试；Python 侧至少过
   `python -m compileall -q <改动目录>`
3. 一个提交做一件事；commit message 中文英文均可，说清"为什么"
4. 提 PR：按模板勾选（含"已读 docs/02"）；关联 issue
5. CI：`ci.yml`（typecheck + compile）与 `secret-scan.yml`（gitleaks）必须绿
6. **不要提交密钥**：`.env`、`~/.hermes/` 内容、任何 `sk-` 开头的字符串

## 6. 代码风格

- TypeScript：跟随仓库现状（nanostores 状态、表驱动优于条件阶梯、插件走 SDK 贡献点）
- Python：跟随内核现状（ruff 配置见 ruff.toml）
- 注释写约束与原因，不写流水账；移植/借鉴的代码注明来源（如
  "移植自 dsh-design-mode storage.ts"）

## 7. 许可

贡献即表示同意你的代码以本仓库的 MIT 许可发布。上游归属见 `LICENSES/`，请勿改动
归属文件。
