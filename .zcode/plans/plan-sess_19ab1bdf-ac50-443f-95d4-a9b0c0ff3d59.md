# 新版 gugu 建设计划（最终版 v3）

## 核心决策（已定）
- **基座：Hermes（NousResearch hermes-agent，MIT，Python 内核 + Electron/React 桌面）**，深 fork。
- **MVP 原则：功能优先——不减重、不做全量品牌改名，先有功能先能用**；减重/改名/品牌化/开源基建全部推迟到"进阶阶段"（v1 可用之后）。
- **唯一的必改项：桌面端内部 Nous 登录体系 → 替换为我们自己的**，模型流量切到 api-net 聚合网关，确保自家平台可用。
- 无自建云后台；登录采用国产 Agent 通用模式（打开网页授权 + loopback 回调），授权页后续并入 api-net。
- 顺序：Work → Design（对标即梦标准）→ IDE。构建期不回看旧失败产品，发布后复盘。

## 第 0 步：下载参考项目（批准后执行，走 40000 代理，失败则直连重试）
```bash
mkdir -p /d/work/projects/joydove/refs && cd /d/work/projects/joydove/refs
git clone --depth=1 https://github.com/NousResearch/hermes-agent.git    # 基座本体
git clone --depth=1 https://github.com/deepseek-ai/deepseek-harness.git
git clone --depth=1 https://github.com/KaichenCurry/dsh-design-mode.git
git clone --depth=1 https://github.com/sst/opencode.git
git clone --depth=1 https://github.com/kgoedecke/doop.git
git clone --depth=1 https://github.com/openai/codex.git
git clone --depth=1 https://github.com/zai-org/ZCode.git                # 仅参考形态
git clone --depth=1 https://github.com/Comfy-Org/ComfyUI_frontend.git
```

## 第 1 步：原样深 fork（1 天）
- 选定 Hermes 版本 tag，全量复制进 `dev/gugu`，独立 git 仓库、**不挂 upstream 远程**（旧事故红线，只留人工会看上游 diff 的习惯）；
- 保留 LICENSE/THIRD_PARTY_NOTICES（MIT 合规，开源阶段前不动内部命名）；
- 跑通 dev 启动（web_server + desktop），确认构建链（pnpm + uv/PyInstaller）在本机可用。

## 第 2 步：登录体系与模型接入替换（MVP 核心，1~2 周）
已知 seam 收敛（源码勘察确认）：`auth.py`、`nous_account`、`nous_auth_keepalive`、`nous_subscription`、`model_setup_flows`、`model_catalog`、`portal_cli` 及 web_server 的 auth/OAuth 端点、桌面 renderer 登录门。工作项：
1. **账号层**：剥离 Nous Portal OAuth（device_code/PKCE）与订阅体系，替换为 gugu 自有 auth 抽象——打开浏览器授权页 → loopback 下发凭证 → 本地存 api-net key（`sk-xxx`）；授权页地址做成配置项，本期可先指向"粘贴 key"简易页，api-net console 集成后切换；
2. **模型层**：`model_catalog`/provider 配置指向 `https://ai.joydove.cn/v1`（OpenAI 兼容；Hermes 若走 Anthropic 形态则用 api-net `/v1/messages`），移除对 inference.nousresearch.com 的依赖；
3. **验证**：流式对话、tools 调用、`/v1/images`、`/v1/videos` 异步任务、`/v1/balance` 欠费 402 反馈全链路实测；
4. 遥测/上报模块中 Nous 域名一并断开（只断不改名）。
- 验收判据：全新用户无任何 Nous 账号，仅凭 api-net key 完成登录→对话→出图→出视频。

## 第 3 步：Work 模式（功能优先，1~2 周）
- 直接用 Hermes 现成能力：会话历史、技能、记忆、终端/文件工具、权限审批；只做三模式外壳的 Work 入口与稳定性打磨；
- 模型选择器接 api-net `/v1/models`（providers/pricing），余额显示；
- Windows 安装包可发内测（更新通道沿用基座现有机制，MVP 不自建发布工程）。

## 第 4 步：Design 模式（对标即梦，3~5 周）
- 进入本阶段先做对标调研：即梦/可灵公开功能资料 + dsh-design-mode/Doop/ComfyUI_frontend 源码，产出《Design 功能对标清单 v1》，逐条定"追平/差异化/暂缓"；
- 无限画布：Konva + React（缩放/框选/旋转/局部撤销/素材落画布）；吸收 dsh-design-mode 三件套：生成前引导式意图问答、选中图像的上下文工具（局部重绘/扩图/超分/抠图）、画布批注回写会话按序处理；
- 生成链路：图像走 api-net `/v1/images`；视频走 `/v1/videos` 异步任务面板（文/图生视频、首尾帧、延长，进度轮询+产物转存本地项目目录）；
- 故事板（即梦核心体验）：脚本→自动分镜→逐镜生成→ffmpeg 本地拼接成片；
- v1 铁边界：不做时间线手工剪辑、多人协作、团队权限；工程文件本地持久化+历史版本+种子复用。

## 第 5 步：IDE 模式（最后，3~4 周）
- 复用 Hermes 现成终端/文件/LSP 工具 + CodeMirror，补编辑器面板与 diff 审批；三模式统一切换框架收敛。

## 第 6 步：进阶阶段（v1 可用后启动）
- 减重（按模块 LOC 出《减重清单》，一次性大删除）；
- 品牌改名清洗（《命名对照表》+脚本化一轮 pass + CI grep 门禁，两轮内清零）；
- 开源发布工程（README/CI/staging 双通道更新）；
- **此时复盘 `close/gugu`、`close/spark-flow`**，吸收验收用例与踩坑记录。

## 风险与对策
- **Nous 登录残留耦合**（如订阅/套餐逻辑散落在 billing 相关 mixin）→ 第 2 步先全库 grep nous/portal 域名与函数调用点列成替换清单，逐点断链并实测；
- **不减重的风险显性化**：死重模块（消息网关等）保持存在但入口隐藏，接受包体积（旧版 ~233MB）换迭代速度，减重留给进阶阶段一次性偿还；
- api-net 视频供货当前仅阿里云 → 即梦级多镜头按厂商入驻解锁；
- 更新/安装链路沿用基座机制 → 内测期人工分发，进阶阶段再工程化。