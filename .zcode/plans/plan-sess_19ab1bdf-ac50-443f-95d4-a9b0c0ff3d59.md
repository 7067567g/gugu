# gugu 三模式架构计划 v2（Design 模式化重构）

## 决策依据（本轮探索结论）
- **壳不需要重写**：Hermes 桌面已有全部所需机制——`ROUTES_AREA` 整页、`layouts` 预设 + `applyLayoutPreset`、`panes` 贡献（placement/dock/enforce，Bots 插件是完整先例）、`titleBar.*`/`workspace.pageHeader` 槽、peer 独立窗。缺的只是"应用级模式"这一层薄状态与切换器。
- **不从零做设计系统**（你的意见 #2）：以 **dsh-design-mode**（MIT，TS+Konva+React，与桌面端同栈）为移植源——它的 `DesignCanvas`(504行)/`DesignSurface`(1166行)/`controller`(1125行)/`storage`(400行) 是成熟实现，逐模块移植改造；万镜一刻/即梦只补"我们没有的能力"（技能芯片、模板卡、故事板流程）。
- **profile 方案否决**：三 profile = 三个后端子进程 + 切换整窗 reload，profile 是后端隔离原语不是 UI 模式原语。模式 = UI 概念，同壳独立 UI 树；独立窗（peer 窗带路由种子）留作后续演进。
- **文档先行**（你的意见 #3）：每个里程碑先写/更新文档再动代码，关键约束固化为代码注释 + memory。

## 里程碑

### M0：架构文档（先行，0.5 天）
- 新建 `docs/02-三模式架构.md`：目标架构图（一壳 × $appMode × 三 UI 树）、扩展点清单（全部带 file:line）、决策记录（为什么不用 profile/不重写画布/移植 dsh-design-mode 的理由）、"如何加第四种模式"操作手册
- 更新 memory：三模式架构决策与 seams
- 现有 `docs/01-Design功能对标清单-v1.md` 补充"移植映射表"：dsh-design-mode 每个文件 → gugu 对应位置

### M1：壳层模式切换（1~2 天）
- `src/store/app-mode.ts`（新）：`$appMode = atom<'work'|'design'|'ide'>`，localStorage 持久化，切换副作用 = `navigate(路由)` + `applyLayoutPreset(模式预设)`
- 切换器 UI：`SegmentedControl`（组件现成）放**侧栏顶部**（"收起侧边栏"按钮旁，按你的要求），Work/设计/编程 三项
- 布局持久化泛化：`src/lib/layout-persistence.ts:72` 的二值 `keyFor`（simple/advanced）改为按 appMode 表驱动——每模式记住自己的布局
- 模式注册表（`src/app/modes.ts` 新）：声明式 `{id, route, presetId, icon}`，IDE 模式先占位（路由指向现有 chat+files 布局）
- 验收：三档切换各自恢复布局与页面，重启记忆上次模式

### M2：Design 应用层移植（3~5 天，核心）
**前端**（`src/plugins/gugu-design/` 重构为模式应用）：
- 移植 dsh-design-mode 的 `DesignCanvas.tsx`（Konva Stage：指针缩放/空格平移/框选/Transformer/评论锚点）与 `controller.ts`（Host 权威 + 乐观状态 + undo/redo 快照栈 + 串行 mutation 队列）——替换我此前写的玩具版 canvas
- Design 布局预设：画布居中 workspace、**资产面板**升格为 `panes` 贡献（placement right）、sessions 侧栏 resting、`workspace.pageHeader` 挂画布工具栏（形状/文字/便签/对齐/缩放）
- 聊天不消失：Design 模式下保留窄会话栏（万镜一刻"对话+画布"并存形态），画布批注回会话沿用现有 composer context 机制

**后端**（`plugins/gugu-design/dashboard/plugin_api.py` 升级为主机纪律）：
- 移植 dsh-design-mode `storage.ts` 纪律：document.json 原子写(temp+rename) + `journal.ndjson` 审计 + magic 校验/symlink 防护 + 尺寸上限
- 每会话串行队列（mutationTails/jobTails promise 链）+ `operationId` 幂等 + `revision` 乐观锁（已有雏形，补齐）
- 崩溃恢复：running→queued（首次 read 时）
- jobs 从 2s 轮询升级为 `pluginSocket('/events')` 推送（后端加一个 WS 路由）
- 生成链路不变：图像 `/v1/images`、视频 `/v1/videos`（已打通真实渠道）

### M3：引导式意图协议（1~2 天，即梦/万镜体验之魂）
- 移植 dsh-design-mode 的 intent 协议：生成前 3 轮（上限 5）"1 问 + 恰 3 选项"，**服务端硬校验**（进度不足拒绝生成工具调用，非提示词自律）
- Hermes 侧载体：`ask_user` 类工具 + 宿主 intentProgress（Python 实现，注释写清与 dsh 的对应关系）
- 前端专用卡片：三选一 + "其他"自由输入 + 进度点

### M4：资产面板 + 上下文芯片（2 天）
- 资产面板：项目素材/成片列表、搜索、拖入画布（对应万镜"资产"）
- 上下文芯片（懒解析）：选中图只挂芯片，提交时才物化为 [元数据+裁剪图+mask]——省 token 且行为可解释

### M5：故事板（3 天，借鉴万镜故事板2.0）
- 脚本→LLM 分镜（每镜 prompt/时长/景别）→画布分镜组→逐镜生成（复用 M2 job）→ffmpeg 本地拼接成片
- 技能芯片行（首页推荐技能：/短剧创作 /商品种草…）作为 H1 技能包的 UI 入口

## 明确不做
- 三 profile 承载模式；Design 从零自研画布；多人协作；时间线剪辑；3D 导演台（远期）

## 风险
- dsh-design-mode 是 DSH 深 fork，移植时只取 `ui-design-mode`/`design-mode` 两包的**源码逻辑**（MIT 允许），不引入其 cordis 依赖——全部改写为 Hermes 插件 SDK 形态，工作量已在 M2 估算内
- 壳层改动（M1）触碰 `layout-persistence`/`routes` 等核心文件，改动量控制在表驱动级别，每处写注释锚定"为什么"
- 网关生产部署（含上一轮熔断修复 + modality 参数）仍需你跑 `deploy/deploy.sh`，本地验证不阻塞