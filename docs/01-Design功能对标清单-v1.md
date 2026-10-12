# gugu Design 模式 · 功能对标清单 v1

对标对象：**万镜一刻（yikeai.com）**（用户指定的标准），辅以 dsh-design-mode / Doop / ComfyUI_frontend 的开源实现。
调研时间：2026-10-12。来源：yikeai.com 官网快照、open.yikeai.com 开放平台与开发者文档（API 参数级）、refs/ 本地源码。

## 1. 万镜一刻产品模型（事实记录）

### 1.1 信息架构（侧边栏）
首页（Agent 对话入口）· Agent(Beta) · 资产 · AI生成(Wan3.0) · **画布** · **故事板 2.0** · **剪辑** · 开放平台

### 1.2 首页主交互
- 大输入框「你想创作什么，告诉 Agent」+ 四 tab：创作/视频/图片/音乐
- **`/` 斜杠技能**（如 `/图像智能超分`、`/短剧创作`、`/资讯播报`、`/商品种草`、`/多宫格生视频`）——技能即场景工作流
- **画布开关**：对话与画布联动（不是独立 app）
- 上传素材（图/文件/剧本）直接进对话
- 亮点功能：Wan 3.0「直出30秒」、故事板2.0「短剧重制一键出海」、**无限画布 3D 导演台**（海量白模、精准参考）
- 场景模板分类：影视创作（电影/短剧）、营销（商品种草/APP推广）、创意（暖心故事/创意美学）、商品展示

### 1.3 能力体系（开放平台三层）
| 层 | 内容 |
|---|---|
| 原子能力 | AI 生成（图/视/音）· AI 编辑（图片编辑、视频编辑、字幕与配音、画质增强、画面擦除、音色复刻）· 媒资管理（存储/分析/搜索/智能标签） |
| 场景能力 | 资讯播报、商品种草、爆款复刻、智能混剪、故事板成片、视频翻译、数字人、短剧重制 |
| 业务体系 | 工作空间（成员/权限/配额）、项目（任务/协作/交付）、资产中心（素材/成片/版本）、用户 |

### 1.4 交付方式（对 gugu 最有参考价值）
- **MCP**：`https://api.yikeai.com/api/agent/mcp/yike-all`，工具自动发现 + 自然语言调用 + 多步骤任务编排
- CLI（自动化工作流）、API（RESTful，Job 提交/查询模式）
- 企业套件：画布 iframe 嵌入、品牌全站、单工具嵌入（统一登录+结果回传）
- Skill 生态：上传 Skill → 调试 → create-skill-api 发布为标准 OpenAPI

### 1.5 生成 API 参数面（任务模型设计参照）
- 视频 `SubmitVideoGenerationJob`：JobType ∈ {text_to_video, image_to_video, first_last_frame, reference_to_video}；Model ∈ {Wonder-Ultra/Pro/Standard, wan3.0-video, happyhorse-1.1}；Resolution 720P/1080P；AspectRatio 16:9/9:16/4:3/3:4/1:1/21:9；Duration 4–15s；N≤4；Input={Prompt, Medias[{Type:image/video/audio, Url/MediaId}]}（reference 最多 9–15 个；真人人脸需先 ImportMedia 拿 MediaId）；JobParameters={EnableAudio}
- 图片 `SubmitImageGenerationJob`：text_to_image / image_to_image（最多 9 图）；Model qwen-image-3.0；Resolution 1K/2K/4K
- 状态机：Created → Queuing → Executing → Finished/Failed（ErrorMessage）；Output=Medias[{MediaId, OutputUrl}]
- 计费：任务级积分（GetYikeJobCredit 查实际消耗；GetYikeAccountCredit 查会员+余额）

## 2. gugu Design v1 功能清单（判定：追平 / 差异化 / 暂缓）

### 2.1 画布核心（追平万镜一刻「画布」+ dsh-design-mode 交互）
| # | 功能 | 判定 | 说明 |
|---|---|---|---|
| C1 | 无限画布：平移/缩放（指针中心）/框选/多选/移动/缩放/旋转/锁定/隐藏/fit-to-view/局部撤销重做 | 追平 | Konva + react-konva，移植 dsh-design-mode `DesignCanvas.tsx`(504行)+`controller.ts`(1125行) 的 controller+thin-view 架构 |
| C2 | 生成前**引导式意图问答**（3–5 个决策点，ask_user 形态） | 追平 | dsh-design-mode 核心 UX；gugu 用 Hermes 原生 tool 审批/提问机制承载 |
| C3 | 选中图像的**上下文工具**：超分/扩图(outpaint)/抠图/局部重绘(inpaint)/文改字 | 追平 | 工具按 api-net 供货排期逐个点亮；调用统一走网关 |
| C4 | **画布批注回写会话**（点/框/刷选区 → 按序处理，保留推理记录） | 追平 | dsh-design-mode 招牌；gugu 会话即 Hermes session，天然可承载 |
| C5 | 素材落画布（拖拽上传/对话内图片一键上屏） | 追平 | 对应万镜「上传图片或文件」+ AddToCanvasAction |
| C6 | 工程文件本地持久化 + 版本历史 + 种子/参数复用（重新生成） | 差异化 | 桌面端本地项目目录（吸取 spark-flow 教训），万镜是云端资产 |
| C7 | 画布 ⇄ 对话联动开关（万镜首页形态） | 追平 | Work 会话中可「展开为画布」 |

### 2.2 生成链路（参数面按 1.5 设计）
| # | 功能 | 判定 | 说明 |
|---|---|---|---|
| G1 | 文生图/图生图（同步，网关 /v1/images） | 追平 | 已实测通（mock 渠道）；真实模型随厂商入驻点亮 |
| G2 | 文/图生视频、首尾帧、多模态参考（异步任务卡片：Created→Queuing→Executing→Finished，进度+产物落盘） | 追平 | 网关 /v1/videos 已实测真实任务 completed+19MB 产物 |
| G3 | 任务卡片参数面：分辨率/宽高比/时长/N/参考图（≤9） | 追平 | 直接采用 1.5 的参数枚举 |
| G4 | 音频开关（EnableAudio）、音色/配音 | 暂缓 | 等网关 TTS 渠道接入后并入 |
| G5 | 视频编辑/延长/混剪 | 暂缓 | v1 铁边界 |

### 2.3 故事板（万镜核心场景「故事板成片」）
| # | 功能 | 判定 | 说明 |
|---|---|---|---|
| S1 | 脚本→自动分镜（LLM 生成 shot list：每镜 prompt/时长/景别） | 追平 | 画布上以分镜组呈现 |
| S2 | 逐镜生成（图或视频）→ 状态看板 | 追平 | 复用 G1/G2 任务卡 |
| S3 | 一键拼接成片（本地 ffmpeg，无云成本） | 差异化 | 万镜走云端渲染；gugu 桌面本地化 |
| S4 | 短剧重制/一键出海/数字人 | 暂缓 | 场景包形态，v2 以 skill 提供 |

### 2.4 资产与技能
| # | 功能 | 判定 | 说明 |
|---|---|---|---|
| A1 | 资产面板（项目内素材/成片列表 + 搜索 + 分类） | 追平 | 本地项目目录索引 |
| A2 | `/` 斜杠技能（短剧创作/商品种草…） | 差异化 | Hermes skills 体系天然就是它，且本地可自定义 |
| A3 | 画布暴露为 MCP server（外部 Agent 可驱动 gugu 画布） | 差异化 | Doop 思路；Hermes 已有 MCP 基建 |

### 2.5 明确不做（v1）
剪辑时间线、多人协作、3D 导演台、团队权限/配额、云端资产同步、在线发布社区。

## 3. 技术架构映射
- **前端**：`packages/gugu-design/`（新）——controller（文档模型：elements/versions/comments）+ DesignCanvas（Konva Stage）+ DesignSurface（工具条/上下文工具/批注）；接入 apps/desktop 新视图「Design」
- **后端**：`hermes_cli/web_routers/gugu_design.py`（新）——项目 CRUD、生成任务提交/轮询（浏览器不直连 provider，全部经 Host 服务排队，学 dsh-design-mode）、产物落项目目录
- **模型层**：api-net `/v1/images` `/v1/videos`（已验证）；任务状态归一化为 1.5 的状态机
- **持久化**：`<project>/.gugu-design/`（project.json + assets/ + versions/）

## 4. 实施切分（第 4 步执行顺序）
1. 4-B 骨架：controller + 画布渲染 + 桌面路由接入 + 本地持久化（无生成）✅ 已完成
2. 4-C1 图像链路：文生图/图生图 + 任务卡 + 素材落画布 ✅ 骨架完成（真实渠道已打通）
3. 4-C2 视频链路：异步任务卡（含首尾帧/参考图）+ 产物落盘 ◐ 后端完成，UI 待 M2
4. 4-C3 故事板：分镜生成 + 逐镜任务 + 本地拼接（→ 新计划 M5）
5. 4-C4 交互精修：引导式意图 + 上下文工具（按供货点亮）+ 批注回会话（→ 新计划 M3/M4）

## 5. dsh-design-mode 移植映射表（M2 执行依据）

移植源：`refs/dsh-design-mode`（MIT）。只取源码逻辑，**不引入 cordis 依赖**——
Remote 调用改 `ctx.rest`（pluginRest），事件改 `pluginSocket('/events')`，
observable 改 nanostores。

| dsh-design-mode 文件 | 行数 | → gugu 目标位置 | 改造要点 |
|---|---|---|---|
| packages/design/design-mode/src/types.ts | ~180 | src/plugins/gugu-design/types.ts | 原样（纯类型，去 Branded）|
| packages/client/ui-design-mode/src/client/DesignCanvas.tsx | 504 | src/plugins/gugu-design/canvas.tsx | 替换现有玩具版；remote→rest |
| packages/client/ui-design-mode/src/client/controller.ts | 1125 | src/plugins/gugu-design/controller.ts | HostObservable→nanostores；Remote→pluginRest；保留 undo/redo 栈与串行队列 |
| packages/client/ui-design-mode/src/client/DesignSurface.tsx | 1166 | src/plugins/gugu-design/surface.tsx | slot 注入→workspace.pageHeader + panes 贡献 |
| packages/client/ui-design-mode/src/client/activation.ts | 172 | src/plugins/gugu-design/activation.ts | modeEnabled 挂 document |
| packages/client/ui-design-mode/src/client/canvas-export.ts | 344 | src/plugins/gugu-design/canvas-export.ts | 原样移植（PNG 导出）|
| packages/design/design-mode/src/storage.ts | ~400 | plugins/gugu-design/dashboard/gugu_storage.py（新）| Python 逐条平移：原子写/journal/magic/symlink 防护 |
| packages/design/design-mode/src/index.ts（服务部分）| ~900 | plugins/gugu-design/dashboard/plugin_api.py | 补串行队列/幂等/崩溃恢复/WS 推送 |
| packages/design/design-mode/src/providers.ts | ~200 | plugins/gugu-design/dashboard/gugu_providers.py | 改为 api-net 网关（去 FAL/OpenAI 直连）|
| ask_user intent 协议（interaction/user-questions + tool-ask-user）| — | M3：Python 宿主 intentProgress + ask_user 工具扩展 | 服务端硬校验（协议强制非提示词自律）|

架构总纲见 `docs/02-三模式架构.md`（模式切换、布局预设、后端门）。
