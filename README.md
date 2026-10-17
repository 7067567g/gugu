# 咕咕 gugu

**Work / Design / Code 三模式一体的开源 Agent 工作台。**

咕咕以 [Hermes Agent](https://github.com/NousResearch/hermes-agent)（MIT）为底座，继承其自进化
Agent 内核（会话记忆、技能沉淀、子代理、定时任务、终端/浏览器工具），并借鉴
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的插件化架构与模式隔离思路、
[Doop](https://github.com/kgoedecke/doop) 的人机共编无限画布范式、
[万镜一刻](https://www.yikeai.com/) 的创作工作流体验，把「干活、设计、编程」装进同一个桌面应用。

## 三种模式，一个壳

| 模式 | 是什么 | 状态 |
|---|---|---|
| **工作 Work** | 通用 Agent 工作台：对话、文件、终端、浏览器、技能与记忆，全在会话里发生 | ✅ 可用 |
| **设计 Design** | 无限画布创作：文生图/图生图、文/图生视频异步任务、素材落画布、本地工程持久化；对话与画布并存 | 🚧 画布移植中 |
| **编程 Code** | 代码工程模式：编辑器、diff 审批、LSP（复用内核编码能力） | 📋 规划中 |

顶栏左角的三档切换器负责在模式间跳转，每个模式有自己的布局与工具面板——
**模式是 UI 概念，不是后端隔离概念**：一个 Electron 壳、一个 Agent 内核、三棵 UI 树。

## 架构一览

```
┌──────────────── Electron 壳（一壳三模式）────────────────┐
│  $appMode: work | design | code                          │
│  每模式独立布局预设 · 咕咕网页授权登录                     │
├─────────────────┬───────────────────────────────────────┤
│ tui_gateway     │ 插件 REST 门 /api/plugins/<name>/      │
│ (会话/agent RPC) │ (Design 文档/资产/生成任务 + WS 事件)   │
├─────────────────┴───────────────────────────────────────┤
│        OpenAI 兼容聚合网关（chat / images / videos）      │
└─────────────────────────────────────────────────────────┘
```

权威文档：[docs/02-三模式架构.md](docs/02-三模式架构.md) —— 改任何模式相关代码前必读。

## 版本

| gugu 版本 | 上游底座（hermes-agent） | 桌面包 | 说明 |
| --- | --- | --- | --- |
| gugu-v0.1.0 | 0.21.5 | 0.17.6 | 首个自有版本：T-02 审批结果集+审计对、T-01 委派持久目录、T-03 计划模式持久状态+exit_plan_mode |
| gugu-v0.2.0 | 0.21.5 | 0.17.6 | T-01 MCP 传输关闭活性+404 重初始化、T-02 预算硬绑定+可重放 token 计量、T-03 统一密钥脱敏 |
| gugu-v0.3.0 | 0.21.5 | 0.17.6 | T-01 token 计量接入 preflight + 阻塞超窗硬拒发、T-02 统一脱敏接入压缩摘要与 owned 凭据持久化 |

## 快速开始

```bash
git clone https://github.com/joydove/gugu.git && cd gugu
uv sync                          # Python 内核
npm ci --legacy-peer-deps        # 桌面端依赖
cd apps/desktop && npm run dev   # 起桌面端
```

完整流程（含模型接入与常见坑）见 [docs/03-getting-started.md](docs/03-getting-started.md)。

## 模型接入

咕咕不绑定任何单一模型厂商：所有模型流量走 **OpenAI 兼容聚合网关**
（chat、图片、视频、语音全模态），桌面端内置「网页授权登录」完成接入。
乐鸽聚合平台目前处于内测阶段，API key 通过申请/邀约获取；
你也可以把 `model.base_url` 指向任意自建兼容网关（详见 docs/03 §2）。

## 许可与归属

- 本仓库自有代码：[MIT](LICENSE) © 2026 JoyDove & gugu contributors
- 底座：Hermes Agent © Nous Research（MIT，全文见 [LICENSES/MIT-hermes-agent.txt](LICENSES/MIT-hermes-agent.txt)）
- Design 模式纪律与画布组件移植参考：DSH Design Mode © DeepSeek（MIT，见 [LICENSES/MIT-dsh-design-mode.txt](LICENSES/MIT-dsh-design-mode.txt)）
- Doop / 万镜一刻 / ComfyUI 等为设计思路借鉴，未复制代码

## 参与共建

- 路线图与待认领任务：[docs/04-roadmap.md](docs/04-roadmap.md)
- 贡献流程：[CONTRIBUTING.md](CONTRIBUTING.md)（核心原则：**文档先行**）
- 安全问题：[SECURITY.md](SECURITY.md)

## 免责声明

应用中 AI 生成的图片/视频/文本内容由模型产生，不代表本项目立场；请谨慎甄别。
