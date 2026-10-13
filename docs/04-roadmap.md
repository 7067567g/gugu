# 路线图（Roadmap）

状态标记：✅ 已完成 · 🚧 进行中 · 📋 计划中 · 💤 求认领（good first issue 候选）

## 已完成 ✅
- **深 fork 基座**：Hermes v2026.9.24（MIT），独立仓库演进，保留上游归属（LICENSES/）
- **咕咕登录体系**：桌面端 Nous 登录替换为自有网页授权（`/gugu/auth/page` 自托管授权页 +
  device_code 形态复用 onboarding 状态机）；Nous 匿名铸号/保活/目录拉取默认关闭
- **模型模态收口**：网关 `/v1/models?modality=` + 内核探测按模态过滤（聊天列表不再混入
  embedding/image/video）
- **三模式切换器**：顶栏「工作 / 设计 / 编程」+ 每模式布局预设（docs/02）
- **Design 后端主机纪律**：原子写 + journal 审计 + MIME magic 校验 + 路径容器化 +
  每工程串行队列 + operationId 幂等 + 崩溃恢复 + /events WebSocket（移植 DSH Design Mode 语义）
- **品牌基础**：应用名/图标/协议/NSIS/exe 元数据咕咕化；boot 屏「咕咕 GUGU」

## 进行中 🚧
- **M2b Design 画布移植** ✅ 主体完成（2026-10-13）：归一化注释锚点、Transformer、
  平移/缩放/锁定/隐藏、fit-to-view、串行保存与幂等（docs/02 §5 有交付清单）；
  剩余：资产面板独立窗格、视频缩略帧、mask 笔刷注释

## 计划中 📋
- **M3 引导式意图协议**：生成前 3~5 轮「1 问 + 3 选项」服务端硬校验（协议强制，非提示词自律）
- **M4 资产面板 + 上下文芯片**：项目素材/成片检索；选中图只挂芯片、提交时懒解析物化
- **M5 故事板**：脚本 → LLM 分镜 → 逐镜生成 → ffmpeg 本地拼接成片；首页技能芯片行
- **Code 模式深化**：编辑器面板、diff 审批、三模式统一切换框架收敛
- **画布 MCP server**：画布能力暴露为 MCP 端点，外部 Agent 可驱动（借鉴 Doop）
- **打包与分发**：Windows 内测包流水线、更新通道（自有源）

## 求认领 💤
- 💤 Design 画布右键菜单（复制/另存/删除/置于顶层）
- 💤 画布键盘快捷键帮助浮层（`?` 呼出）
- 💤 getting-started 的 macOS/Linux 实测反馈
- 💤 i18n：`src/i18n/` 非中英语言的咕咕术语校对

> 认领前请先读 [docs/02-三模式架构.md](02-三模式架构.md)，PR 遵循
> [CONTRIBUTING.md](../CONTRIBUTING.md) 的「文档先行」原则。
