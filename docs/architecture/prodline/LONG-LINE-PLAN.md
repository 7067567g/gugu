---
schema: gugu.long-line-plan/v1
id: gugu-long-line
title: gugu 长线计划 — 上游 re-baseline / 产线化 P1–P3 / 工作流适配
owner: MAIN（plan 轮）
status: active
created: 2026-10-25
baseline:
  repo: gugu-work
  commit: "6737fff0"
  product_version: "0.8.0"
  kernel_version: "0.21.5"
upstream:
  repo: NousResearch/hermes-agent
  policy: stable-tag-only
  first_target: v0.22.5
  first_target_commit: bf0203a794a07aac3ff764972ab9087baa4e4f4e
horizon: open-ended（不设总轮数上限；按阶段验收后追加）
next_review: Phase 1 结束（gugu-reb-r08）后的计划评审轮
supersedes: []
---

# gugu 长线计划（LONG-LINE-PLAN）

> 这是**计划**，不是实现。本文件只描述阶段、轮次、边界、门禁、证据、回滚与停止条件；
> 本轮（`gugu-plan-longline`）不写业务代码、不改 `install.ps1`、不建 `prodline/`、不动 `pyproject.toml`。
>
> 输入：`/home/a7067567g/下载/deepseek/月月bot/gugu-upstream-eval-20261025.md`（下称 **EVAL**）、
> `/home/a7067567g/下载/deepseek/月月bot/gugu-prodline-arch-plan-20261025.md`（下称 **ARCH**）、
> `runs/gugu-arch-r02-ci/reports/`、`runs/gugu-arch-r03-verify/reports/`。
> 事实口径：本轮实测优先于两份输入报告；凡未复跑者标 **未验证**，不写成通过。
> 证据：`runs/gugu-plan-longline/reports/`（`plan-facts.txt`、`rebaseline-rehearsal.txt`、`DOC-CHECK.txt`）。

---

## 0. 读法与一页结论

### 0.1 四条同时成立的结论

1. **核心适配先做，但不能“直接 merge”**。当前 gugu 的分叉点 `171fdfd7` 是一个
   **root commit（无父提交）**，提交信息写着 “fork hermes-agent v2026.9.24 as gugu base
   (MIT, deep fork, no upstream tracking)”；它的树与上游 `v2026.9.24` 的树
   15,048/15,051 个路径逐字节相同，但**没有共享历史**。所以 re-baseline 必须是
   “在新 tag 上重放补丁队列 + 逐文件三方合并”，不是 `git merge upstream/v0.22.5`。
2. **接触面确实有上限，但“4 处符号漂移”是 main 口径，不是 v0.22.5 口径**。
   本轮在目标 tag 上复测：`browser_tool_lifecycle` / `browser_tool_cdp` 的改名提交
   `855f03e3f7` **晚于 v0.22.5**，两个浏览器符号在 target 上实测不漂移（AST 未命中）；
   `tools/tirith_security.py` 的删除提交 `68dd992769`（2026-10-06）**早于 v0.22.5**，
   所以 `check_command_security` 的 2 处引用是真实漂移；此外还发现
   `_tirith_fail_open`、`warn_once`×6 等 **15 个候选**（详见 §2.3）。
   **4 是已知下限，不是上限；A1 必须用完整 blob 重跑 AST 扫描。**
3. **产线化的第一缺口不是“没有能力”，而是“能力没有产线级所有者”**。
   `prodline/`、`artifacts/`、`contracts/` 都不存在；CI 8 个 job 全在 ubuntu、
   零 Windows、零 build；`install.ps1` 5,190 行里仍有 5 处 `NousResearch/hermes-agent`
   URL（386、387、2542、2545、2548），`install.sh` 另有 2 处，`windows-e2e.ps1` 另有 2 处。
   所以 P1 先建“回执归属层”，P2 再把安装从“clone 上游”改成“装自己的 artifact”，
   P3 才做发行物拆分。
4. **v0.22.5 的冲突面与 main 相同，16 hunk 可以当实测基线**。本轮在 26/26 的 tag blob 上
   完成三方合并：12 干净 / 14 冲突 / 16 hunk，与 EVAL 的 main 口径完全一致；A2 只需在
   干净 clone 上复跑脚本、把报告入库，不再有“8 文件未验证”的缺口。

### 0.2 两条轨道与执行顺序

```text
Phase 0  P0-ARCH（已完成：r01 规则/账本 → r02 CI → r03 独立复核 CLOSE/WARN）
   │
   ├─ 轨道 A：核心适配（kernel 写窗口）
   │   Phase 1  REBASE  →  A1 冻结/清单 → A2 完整演练 → A3 新文件 →
   │                       A4 干净合并 → A5 冲突重放 → A6 漂移/install 归属 →
   │                       A7 测试/门禁 → A8 版本/合并 → 计划评审
   │
   └─ 轨道 B：产线化（prodline 写窗口）
       Phase 2  P1  prodline 骨架 + receipt 归属
       Phase 3  P2  安装从 artifact + Windows build job
       Phase 4  P3  打包拆分（kernel / glue / extensions）
       Phase 5  CADENCE  月度 re-baseline + 产线全链验收（按需追加）

并行规则：A1/A2 只读，可与 P1 并行；A3–A8 是 kernel 写窗口，期间 P1 只能写
prodline/ 与 docs；A6 与 P2 都写 install.ps1 → 串行，单写者。
```

**顺序理由**：P2/P3 的 artifact 与 wheel 都要从某个基线切出来。若先做 P2/P3 再 re-baseline，
同一批产物要切两次、回执要重签、Windows E2E 要重跑；先把基线移到 `v0.22.5`，
后续 artifact 只切一次。P1 只写新目录 `prodline/`，与 kernel 文件零重叠，可以并行。

### 0.3 编号映射（避免与 ARCH 的 P 编号混淆）

| 本计划 | ARCH 的编号 | 状态 | 说明 |
| --- | --- | --- | --- |
| Phase 0 P0-ARCH | ARCH P0 + P1 | **已完成**（r01–r03） | 分类账本 + 棘轮门 + CI `arch-gate` |
| Phase 1 REBASE | ARCH 未单列（EVAL §7） | 未开始 | 上游 re-baseline 到 v0.22.5 |
| Phase 2 P1 | ARCH **P2** | 未开始 | `prodline/` 骨架 + 3 receipt schema + `accept.py` |
| Phase 3 P2 | ARCH **P3 + P4 前半** | 未开始 | `build-win` + `install.ps1 -FromArtifact` + install/runtime receipt |
| Phase 4 P3 | ARCH **P5/P6 的发行物部分**（不是全量搬家） | 未开始 | kernel / glue / extensions 三件发行物 |
| Phase 5 CADENCE | ARCH P7 的一部分 | 持续 | 月度 re-baseline + 全链验收，按需追加 |

> 本计划的 “P1/P2/P3” 一律指 Phase 2/3/4；引用 ARCH 时写 “ARCH-P2” 等，禁止裸写 P2 造成歧义。

### 0.4 不设总轮数上限的规则

1. **阶段固定、轮次开放**：Phase 1–4 的验收边界固定；每阶段需要多少轮由计划评审轮决定。
2. **追加轮次的唯一入口是 backlog**：新增项必须有 `ID + 描述 + owner + 去向 + 最晚复核轮`，
   写进 §6；没有这五项不得开工。
3. **26 只是初估**：EVAL 的“26 文件 / 774 行”是本轮实测的**当前**补丁栈，不是目标轮数，
   也不是上限；若 A2 演练出更多 hunk，加轮次，不砍内容。
4. **砍范围只允许发生在停止条件触发时**（§4.5），并且必须在 `VERDICT.md` 写明砍了什么、
   谁接受的、去向是哪一轮。

### 0.5 术语：L1/L2/L3 是历史标签

input goal 里的 “L1 目标特化 / L2 骨架特化 / L3 计划评审” 是**历史标签**；
`WORKFLOW.md §11` 已把 `L1/L2/L3` 作为规范术语退役。本计划正文使用
**目标特化 / 骨架特化 / 计划评审**，表格保留 “原标签” 一列仅作对照，不作为现行术语。
判定规则见 §4.1。

---

## 1. 基线事实（本轮实测）

### 1.1 仓库、上游、版本

| 项 | 实测值 | 证据 |
| --- | --- | --- |
| `gugu-work` HEAD | `ebca845b`（r03 docs-only 之后） | `git rev-parse --short HEAD` |
| diff 基线 | `6737fff0`（`gugu-v0.8.0` tag） | `gugu-arch-pipeline/BASE-COMMIT` |
| 产品版本 | `GUGU_VERSION = 0.8.0` | `GUGU_VERSION:1` |
| 内核版本（声明） | `pyproject.toml:5 = 0.21.5` | `version-sources.json` 的 `upstream_kernel` 组 |
| 上游 clone | `/home/a7067567g/下载/deepseek/60-外部仓库/hermes-upstream`（partial clone） | `git -C hermes-upstream rev-parse v0.22.5^{commit}` |
| 目标 tag | `v0.22.5`（2026-10-22，stable） | `bf0203a794a07aac3ff764972ab9087baa4e4f4e` |
| 目标 tag 距 main | `541` 个提交 | `git rev-list --count v0.22.5..main` |
| 自有远端 | `origin = github.com/7067567g/gugu.git` | `git remote -v` |
| 现 `upstream` 远端 | `github.com/joydove/gugu.git`（**不是** NousResearch/hermes-agent，push=DISABLED） | `git remote -v` |
| 上游远端 | **缺失**；目标仓没有名为 `hermes`/`hermes-upstream` 的 remote | `git remote -v` |
| 产品 tag | `gugu-v0.1.0 … gugu-v0.8.0`（8 个） | `git tag` |

**结论**：必须先把 remote 语义修好（§2.5），否则任何“fetch upstream”脚本都会 fetch 到
`joydove/gugu`——一个 gugu 镜像，而不是上游 Hermes。这是本轮发现的 **B1** 项。

### 1.2 补丁栈实测（当前 HEAD，相对 fork base `171fdfd7`）

| 口径 | 文件 | 增 | 删 | 说明 |
| --- | ---: | ---: | ---: | --- |
| 修改上游既有文件 | **26** | **+774** | **−65** | 有冲突可能；受 §2.2 上限约束 |
| 新增内核文件（上游无同名） | **9** | **+1,441** | 0 | 结构上零冲突；单独计入“内核总面” |
| 内核合计（5 目录） | **35** | **+2,215** | **−65** | `agent/tools/hermes_cli/gateway/tui_gateway` |
| gugu 测试 | 18 | +3,114 | 0 | `tests/`，re-baseline 的硬回归面 |
| 全仓（含 apps/docs/scripts） | 180 | +12,118 | −11,116 | 含 9 个 arch 提交；内核仍 2,215 行 |

9 个新文件：`agent/plan_mode.py`、`agent/secret_redaction.py`、`agent/token_meter.py`、
`hermes_cli/gugu_service.py`、`hermes_cli/version_source.py`、`hermes_cli/web_routers/gugu_auth.py`、
`tools/approval_outcome.py`、`tools/delegate_catalog.py`、`tools/plan_mode_tool.py`。

命令（可复跑）：

```bash
git diff --numstat 171fdfd7..HEAD -- agent tools hermes_cli gateway tui_gateway \
  | awk '{a+=$1;d+=$2;n++} END{print n,a,d}'          # 35 2215 65
git diff --name-status 171fdfd7..HEAD -- agent tools hermes_cli gateway tui_gateway \
  | awk '$1=="A"{a++} $1=="M"{m++} END{print "A="a,"M="m}'  # A=9 M=26
```

### 1.3 为什么不能 `git merge`（本轮最重要的结构性事实）

| 检查 | 结果 |
| --- | --- |
| `git rev-parse 171fdfd7^` | 无父提交（root commit） |
| `171fdfd7` 提交信息 | `fork hermes-agent v2026.9.24 as gugu base (MIT, deep fork, no upstream tracking)` |
| 树对比 `171fdfd7` vs 上游 `v2026.9.24` | 15,048 相同 / 1 不同 / 2 仅 base / 26 仅 upstream（15,051 vs 15,075 条目） |
| 共享历史 | 无；与任何上游 tag 都没有 merge-base |

**含义**：
- `git merge upstream/v0.22.5` 会得到 unrelated histories，且会把 30 天的上游变更与 gugu 的
  26 文件补丁混在一个冲突里，无法逐 hunk 归因。
- 正确做法是 EVAL §3.3 的**文件级三方合并**：`base = 171fdfd7:f`、`ours = v0.22.5:f`、
  `theirs = HEAD:f`，逐文件 `git merge-file --diff3`，再按 hunk 分类重放。
- 分支策略：从 `v0.22.5` 新开 `rebaseline/v0.22.5`，**不在 main 上 merge**；main 只在 A8
  验收通过后快进/合并该分支（本地，不 push）。

### 1.4 目标 tag 的演练结果（本轮只读实测，26/26 完整）

在 `v0.22.5` 的 26 个修改文件上做了完整三方合并（26/26 的 tag blob 在本地可用）：

| 结果 | 文件数 | 承载行数（theirs） | hunk |
| --- | ---: | ---: | ---: |
| 干净合并 | 12 | — | 0 |
| 冲突 | 14 | — | **16** |
| 新增文件（上游无同名） | 9 | +1,441 | 0（结构上不可能冲突） |

冲突明细（hunk）：`hermes_cli/models.py` 2、`tools/mcp_tool_transport.py` 2；
`agent/context_compressor.py`、`gateway/run_inbound.py`、`hermes_cli/_early_recovery.py`、
`hermes_cli/_startup_fast.py`、`hermes_cli/banner.py`、`hermes_cli/build_info.py`、
`hermes_cli/cli_commands_mixin.py`、`hermes_cli/web_routers/oauth.py`、
`hermes_cli/web_server.py`、`hermes_cli/web_server_oauth.py`、`tools/approval.py`、
`tui_gateway/methods_tools.py` 各 1。

**关键结论**：本轮的 v0.22.5 结果与 EVAL 在**上游 main** 上测到的
12 干净 / 14 冲突 / 16 hunk **完全一致**。main 比 tag 多 541 个提交，但这 26 个文件的
冲突面没有增加——说明 EVAL 的 16 hunk 不是“main 特例”，可以作为 v0.22.5 的实测基线。
即便如此，A2 仍要在**干净 clone** 上复跑本轮的脚本并入库，避免“单机上偶然可合并”的假象。
若 A2 的 hunk 数 > 60 或连续两次 > 3 人日，触发 §4.5 的停止/翻案条件。

原始输出：`reports/rebaseline-rehearsal.txt`；复跑脚本见 §9.2。

### 1.5 符号漂移：4 是已知下限，不是全部

EVAL 在 main 上的口径是“4 个符号 / 2 个文件”：

| # | 符号 | 基点 | main | v0.22.5 实测 | 判定 |
| --- | --- | --- | --- | --- | --- |
| 1 | `tools.browser_tool_lifecycle.cleanup_all_browsers` | 有 | 模块改名 `browser_session_lifecycle` | 模块仍在；改名提交 `855f03e3f7` 在 **tag 之后** | **target 实测不漂移**（AST 未命中）；A1 复核 |
| 2 | `tools.browser_tool_cdp._stop_cdp_supervisor` | 有 | 模块重组 | 模块仍在；同上 | **target 实测不漂移**（AST 未命中）；A1 复核 |
| 3 | `tools.tirith_security.check_command_security`（引用 1） | 有 | 整文件删除 | 删除提交 `68dd992769`（2026-10-06）**早于 tag** | **真实漂移** |
| 4 | 同上（引用 2，`tools/approval.py:1151`） | 有 | 同上 | 同上 | **真实漂移** |

本轮 AST 扫描（限定“基点存在、tag 上消失”的 import，跳过 gugu 自有新模块）得到 **15 个候选**，
其中 EVAL 没列的至少 11 个，主要几类：

| 候选 | 引用点 | tag 上的事实 | 处理方向（A1 定稿） |
| --- | --- | --- | --- |
| `tools.approval_context._tirith_fail_open` | `tools/approval.py:24` | `approval_context.py` 在 tag 上已无 tirith 相关符号 | 与 #3/#4 合并为“tirith 退场”一个决策（§2.3.2） |
| `hermes_cli.plugin_compat.warn_once` | `context_compressor.py:5578`、`turn_context.py:1326`、`commands.py:755`、`models.py:2869`、`web_server.py:1970`、`approval.py:1358` | `plugin_compat.py` 在 tag 上只剩 `compat_report/removal_in_effect/summary_lines` | 找到上游后继；否则在 gugu 自有小模块提供一次性 helper，**不**把旧文件整体搬回 |
| `hermes_cli.banner.UPDATE_AVAILABLE_NO_COUNT` / `check_for_updates` | `_startup_fast.py:227` | tag 上只剩注释/内部调用，不再导出 | 改用上游 update-check 接口，保留 gugu 文案 |
| `hermes_cli.build_info.get_build_sha` | `banner.py:454` | tag 上已无此函数 | 改用上游 build-info 字段或 gugu `version_source` |
| `gateway.run_inbound_unauthorized.PAIRING_RATE_LIMITED_REPLY` | `run_inbound.py:24` | tag 上无此常量 | 跟随上游重命名/新常量 |
| `hermes_cli.update_handoff.detached_shim_child_env` | `main_install_repair.py:440` | tag 上无此函数 | 跟随上游 rename |
| `tools.checkpoint_manager.checkpoint_footprint_notice` | `update_cmd_maint.py:893` | tag 上无此函数 | 跟随上游 rename/删除 |

> 扫描仍有 **136 处 import 因 tag 侧模块 blob 缺失未能判定**（§8 U2）。所以：
> **A1 的漂移门必须补全 blob、0 未解析才允许进入 A3。**
> 这也是把“符号漂移检查”产品化（`scripts/upstream/check_symbol_drift.py`）的原因。

### 1.6 产线化现状事实（与本轮计划直接相关）

| 事实 | 实测 | 含义 |
| --- | --- | --- |
| `prodline/`、`artifacts/`、`contracts/` | 均不存在 | P1 从零建，零侵入 |
| CI job | 8 个（`python-compile`、`arch-gate`、`desktop-typecheck`、`desktop-vitest`、`pytest`、`pytest-suites`、`coverage`、`all-checks-passed`） | 全 ubuntu；无 Windows、无 build |
| `arch-gate` | 已接线，PyYAML `6.0.3`，无 soft-fail | P0 已收口；r03 的 F1/F2/F4 待补 |
| `install.ps1` | 5,190 行 / 96 函数；固定 stage **15** 个 + `desktop` 可选 = **16** | ARCH 写“18 段”，实测 16；该差异进 backlog B11 |
| 上游 URL | `install.ps1` 5 处（386/387/2542/2545/2548）；`install.sh` 2 处；`windows-e2e.ps1` 2 处 | 改“装自己”至少覆盖 install.ps1 这 5 处 |
| `install.ps1` 已有 ZIP 兜底 | `2533–2563`，但 ZIP URL 仍指向上游 | P2 可复用该兜底改成 artifact 下载 |
| `windows-e2e.ps1` | 1,025 行，CI 零引用 | 资产在盘，缺接线 |
| `tests/scripts` | CI 零引用 | footgun 检查器在 CI 不跑 |
| `plugin.yaml` | 106 个；`api_version` / `permissions` / `health` 各 **0** | 扩展契约缺口 |
| `dashboard/manifest.json` | 3 个 | 三注册面之一 |
| `update_receipt.py` | 593 行，`schema: 1`，只覆盖 `hermes update` | 产品回执，不能当产线回执 |
| `pyproject` 打包 | 单个 `include` 列表收 `agent*/tools*/hermes_cli*/plugins*/gateway*/tui_gateway*/providers*/cron*/acp_adapter*` | 无 kernel/extension 发行物之分 |
| `[project.scripts]` | 3 个（`hermes`/`hermes-agent`/`hermes-acp`） | 无 `gugu` 入口；发行物身份仍偏上游 |
| 就绪超时 | `backend-ready.ts:22 = 90_000`；`backend-health.ts:1 = 45_000` | 两个“就绪”定义；P2 必须用 P 自己的探针 |
| 测试规模 | `tests/**/test_*.py` 4,735 个；`tests/ci` 14 个 | 产线全链不能只靠 14 个 |

### 1.7 与两份输入报告的差异（必须显式记录）

| 项 | EVAL/ARCH 口径 | 本轮实测 | 采用 |
| --- | --- | --- | --- |
| 符号漂移数 | 4（main） | v0.22.5 上 4 里只有 tirith 的 2 处真实；另发现 15 个候选 | 4 = 已知下限；A1 以完整扫描为准 |
| 冲突 hunk | 16（main，EVAL） | 16（v0.22.5，本轮 26/26 完整演练） | 两者一致；按 16 估算 |
| X→G 违规数 | 613（ARCH §2.1） | 账本 `X->G = 484`；613 = plugins→hermes_cli 266 + →gateway 218 + →agent 129，其中 →agent 129 属 X→K（**允许边**） | 规则门引用 **484**；解释“扩展反向吃内部”时可用 613 并注明含合法 X→K |
| stage 数 | 18（ARCH §2.2 L6） | 固定 15 + desktop 可选 = 16 | 以 16 为准；差异进 B11 |
| `K→G` | 442（ARCH 的 agent→hermes_cli） | 账本 `K->G = 1052`（agent→hermes_cli 442 是最大单项） | 门禁引用 1052；单项引用 442 |
| 全仓 diff | 179 文件 +11,809/−11,116（EVAL） | 180 文件 +12,118/−11,116（含 9 个 arch 提交） | re-baseline 只关心内核 35 文件 |

---

## 2. 轨道 A — 核心适配：re-baseline 到 v0.22.5

### 2.1 目标与非目标

**目标**：
1. 把 gugu 的 35 个内核文件补丁栈完整落到 `v0.22.5` 的树上，测试、编译、边界门全绿。
2. 建立可重复的**月度 re-baseline 纪律**与**补丁栈棘轮**，让“接触面不增长”可验收。
3. 把 `install.ps1` 从“clone 上游 Hermes”改成“装 gugu 自己的 artifact/仓库”，
   为 P2 的 artifact 安装留好开关。
4. 把符号漂移从“人工记得查”变成“CI 可跑的 0 未解析门”。

**非目标**：
- 不跟 `main`、不跟 `+canary.*`、不跟 `rc.*`（EVAL §7.2）。
- 不重写 `install.ps1` 的 5,190 行，不改 15+1 个 stage 协议。
- 不把 `agent/`→`kernel/` 全量搬家（ARCH §7.2 明确不做）。
- 不在同一轮做 `pyproject` 发行物拆分（那是 Phase 4，且需要单独批准）。

### 2.2 补丁栈边界与上限（棘轮）

| 项 | 当前 | 上限 | 余量 | 规则 |
| --- | ---: | ---: | ---: | --- |
| 修改上游既有文件 | 26 | **≤ 30** | +4 | 每个新增修改必须写理由；触碰 top-4 churn 文件直接触发翻案评审 |
| 修改上游既有文件的新增行 | +774 | **≤ 900** | +126 | 只算 gugu authored diff，不算上游自身代码 |
| 新增内核文件 | 9 | **≤ 12** | +3 | 每个必须有“无扩展点”证明 + `arch/architecture.yaml` 登记 |
| 新增内核文件的行数 | +1,441 | **冻结在 1,441** | 0 | 存量计入基线；未来 12 个月新增内核行 **≤ 100** |
| top-4 churn 文件改动 | 0 | **0** | 0 | `gateway/run.py`、`hermes_cli/main.py`、`hermes_cli/config.py`、`tui_gateway/server.py` |
| 新 Python 依赖 | 0 | **0** | 0 | 能力走插件/注册表；不得为 re-baseline 加依赖 |
| 删除/跳过测试 | 0 | **0** | 0 | 任何测试变更必须说明“为什么不是产品 bug” |

**读法**：
- “修改文件 ≤30 / 新增行 ≤900”是**对外部世界的承诺**，不是本轮的一次性目标；超过即触发
  §4.5 的“接触面扩张”停止条件，进入 A→B 翻案评审。
- 9 个新文件不占 30 文件的额度，但占“内核总面”；新文件不是免费午餐——每个都要在
  `arch/architecture.yaml` 有 owner、理由、契约（或明确“临时内核”）。
- 上限按**相对目标 tag** 计算：re-baseline 后重新数，不是把当前数字带到新基线。

### 2.3 符号漂移处理

#### 2.3.1 处理原则

1. **先测后改**：A1 用完整 blob 跑 AST，输出 `symbol-drift.json`（module/name/file:line/base?/tag?）。
2. **四类处置**，禁止“把上游删掉的模块整体搬回内核”：
   - **rename/move**：改 import 指向上游新位置（首选，成本 1–2 行）。
   - **re-express**：上游把旧结构拆了（如 `models.py` 的 catalog 探测），把 gugu 的语义
     重新表达在新结构上（EVAL §3.6 类型 B）。
   - **adapter**：gugu 自有小模块提供兼容 helper（如 `warn_once`），但必须 ≤50 行、
     有单测、登记为 K、并写明“上游有后继时删除”。
   - **delete**：能力已被上游覆盖（tirith），删除 gugu 的调用与配置，不保留空壳。
3. **每个漂移项必须有测试**：至少 1 个能红后绿的回归测试；测试文件计入 gugu 测试面。
4. **allowlist 只能由计划评审轮批准**：`symbol-drift-allowlist.json` 每条带 reason + owner +
   最晚复核轮；CI 里不允许 `continue-on-error`。

#### 2.3.2 tirith 退场的决策（A6 必须落定）

事实：上游 `68dd992769`（2026-10-06）删除了 `tools/tirith_security.py`，把检测并进
`tools/approval_detection.py`（`detect_hardline_command` / `detect_dangerous_command`），
并在 `tools/approval.py` 只 import `approval_detection` 的符号；gugu 的 `tools/approval.py`
仍在 660/1151 行懒 import `tools.tirith_security.check_command_security`，并从
`approval_context` import `_tirith_fail_open`。在 v0.22.5 上这三者都不存在。

**建议决策（D-005）**：
- **不**把 `tirith_security.py` 作为内核文件搬回（违反“新内核行 ≤100/年”且上游已放弃该路径）。
- 把 gugu 的“fail-closed”语义重新表达为：
  - 用上游 `detect_hardline_command`/`detect_dangerous_command` 的结果作为阻断源；
  - 保留 `security.tirith_fail_open` 配置键一个产品周期，映射到“检测器不可用 → 按配置阻断”
    的 gugu 策略；配置键在 CHANGELOG 标注 deprecated，下一周期删除；
  - 若仍需要外部扫描器，走 **X（插件）**，不进内核。
- 验收：`tools/approval.py` 不再 import `tools.tirith_security` / `_tirith_fail_open`；
  新增 2 个测试：危险命令阻断、检测器不可用时的 fail-closed/fail-open 分支；
  旧配置键迁移测试。

> 这是一个**语义变更**，不是 1 行 import 改指；必须在 A6 的 commit message 和 CHANGELOG
> 里显式写 BREAKING（行为变更），并给回滚开关。

#### 2.3.3 `warn_once` 的处置

`warn_once` 在 gugu 的 6 个 patch-stack 引用点仍在，但上游 `plugin_compat.py` 已无此符号。
A1 先找上游后继（可能在 `hermes_cli/deprecation` 或各 registry 内联）；若无：
- 在 **gugu 自有** `hermes_cli/compat_warn.py`（≤50 行）提供 `warn_once`，6 处 import 改指；
- 该文件登记 K，注明“上游若有后继则删除”，并加 1 个 dedup 测试；
- 不计入“修改上游文件”额度（新文件），但计入“新增内核文件 ≤12 / 新增行 ≤100/年”预算。

### 2.4 re-baseline 步骤（A0–A8）

| 步 | 名称 | 做什么 | 产出 | 门禁 | 回滚 |
| --- | --- | --- | --- | --- | --- |
| **A0** | 冻结与清单 | 确认 main 干净；把 35 个内核文件 + 18 个测试文件的 **base/HEAD blob sha256** 写成 manifest；记录 26/9/774/1441 的实测数字 | `arch/upstream.lock`（新）、`reports/rebase-manifest.json` | manifest 覆盖 35/35；数字与 §1.2 一致；不改产品代码 | 删两个文件 |
| **A1** | remote/tag/漂移基线 | 加只读 remote `hermes`；fetch `v0.22.5`；校验 `^{commit}=bf0203a7`；跑完整 AST 漂移扫描；建 `symbol-drift.json` | remote 配置、`reports/drift-v0.22.5.json` | tag commit 精确；0 未解析或 allowlist 有 reason；不 checkout | 删 remote/分支，工作树不变 |
| **A2** | 完整演练 | 在干净 clone 上复跑本轮已完成的 26/26 三方合并（12 干净 / 14 冲突 / 16 hunk）；逐 hunk 分类（A 并集 / B 重写 / C 品牌）；估工时；把脚本与报告入库 | `reports/rebase-rehearsal-full.md`、可复跑脚本 | 26/26 文件有结论；hunk 表可复算；与 §1.4 的 16 一致（或解释差异）；若 hunk >60 → 停 | 删 scratch worktree |
| **A3** | 新分支 + 新文件 | 从 `v0.22.5` 开 `rebaseline/v0.22.5`；先落 9 个新文件（零冲突）；`py_compile` | 1 个 commit（或 3 个分组 commit） | 9/9 文件在；`py_compile` 全过；无冲突标记 | 删分支 |
| **A4** | 干净合并 | 12 个干净文件按 A2 结果落树；每文件一个 commit 或按目录分组；保留 gugu diff 意图 | 12 个文件 commit | 每文件 diff 与 manifest 的 gugu 行一致；`py_compile` 过 | 逐 commit revert |
| **A5** | 冲突重放 | 14 个冲突文件按类型处理；A 类并集、B 类重写、C 类品牌；每文件一个 commit，message 写 hunk 来源 | 14 个文件 commit | 0 冲突标记；`py_compile` 过；关键文件有测试 | 逐 commit revert |
| **A6** | 漂移收口 + install 归属 | 处理 §2.3 全部漂移；`install.ps1` 5 处 URL 改自己 artifact/仓库 + `-LegacyClone`；`install.sh` 2 处同步 | 漂移 commit + install commit | 漂移 0 未解析；`grep NousResearch/hermes-agent scripts/install.ps1` = 0（注释除外）；stage 仍 16 | 逐 commit revert；`-LegacyClone` |
| **A7** | 测试/编译/门禁 | 跑 gugu 18 个测试文件（3,114 行）、`tests/ci`、`tests/plugins/model_providers`、arch gate、21+ 文件 `py_compile`；修到绿 | `reports/rebase-tests.txt` | 测试绿（或有 reason 的 skip）；gate 需重 seed 时单独 commit | 回到 A6 |
| **A8** | 版本/文档/合并 | `version-sources.json` 的 kernel 组改 0.22.5；CHANGELOG 写 re-baseline；`upstream.lock` 更新；本地合并到 main（不 push）；tag 由发布窗口决定 | `gugu-v0.9.0` 候选（不 push） | 版本断言测试绿；diff 边界正确；计划评审 PASS | 保留 `rebaseline/v0.22.5` 分支 |

**commit 粒度要求**：
- 一个 commit 只做一类事（新文件 / 干净合并 / 单个冲突文件 / 漂移 / install / 测试修理 / 版本）。
- 每个 commit message 必须含：`hunk` 来源（若冲突）、`drift` ID（若有）、`rollback` 一行。
- 禁止“一个大 commit 把 26 文件全放进去”——无法归因、无法回滚、无法 review。

### 2.5 `install.ps1` 改为装自己 artifact

#### 2.5.1 现状

| 位置 | 内容 | 问题 |
| --- | --- | --- |
| `scripts/install.ps1:386-387` | `$RepoUrlSsh/$RepoUrlHttps = …NousResearch/hermes-agent.git` | 默认 clone 上游 |
| `:2519` / `:2528` | `git clone --depth 1 --branch $Branch $RepoUrl*` | 装的是上游代码 |
| `:2542/2545/2548` | ZIP 兜底 URL 仍是 `NousResearch/hermes-agent/archive/...` | clone 失败也装上游 |
| `:2533-2563` | ZIP 下载 → 解压 → `git init` → fetch 上游 | 有可复用的下载/校验骨架 |
| `:4861-4891` | 15 个固定 stage + `desktop` 可选 = 16；`-Stage` 有 JSON manifest 协议 | 协议本身是资产，别重写 |
| `scripts/install.sh:46-47` | 同样硬编码上游 | Linux 安装路径同病 |
| `tests/install/windows-e2e.ps1:136-137` | 同样硬编码上游 | E2E 也指上游 |

#### 2.5.2 分两步走（避免 A6 与 P2 抢写）

- **A6（最小归属修正，本阶段）**：
  1. `$RepoUrl*` 改为 gugu 自己的仓库（`origin`，或固定的 release artifact 仓库）。
  2. ZIP 兜底 URL 改为 gugu 的 release/tag 归档地址；保留 `$Commit/$Tag/$Branch` 优先级。
  3. 新增参数：`-ArtifactUrl <url>`、`-ArtifactSha256 <hex>`、`-LegacyClone`（默认 false）；
     当 `-ArtifactUrl` 给出时，走“下载 → 校验 sha256 → 解压 → 安装”，**不 clone**。
  4. `-LegacyClone` 为 true 时走旧 clone 路径（一个参数回滚）。
  5. 不改 15+1 个 stage 的名称、顺序、manifest 协议；`repository` stage 内部二选一。
  6. `install.sh` 的 2 处 URL 同步改；`windows-e2e.ps1` 的 2 处留给 P2（它属于测试资产）。
- **P2（完整 artifact + 回执，下一阶段）**：
  `-FromArtifact` 接 build artifact、校验 sha256、install-receipt/runtime-receipt、
  readiness 计时、10/10 计数；`windows-e2e.ps1` 接 workflow。

**验收（A6）**：
- `grep -n "NousResearch/hermes-agent" scripts/install.ps1` 只允许出现在注释/迁移说明里；
- PowerShell 语法检查（有 `pwsh` 则 `pwsh -NoProfile -Command "[scriptblock]::Create((Get-Content -Raw scripts/install.ps1))"`；
  无 `pwsh` 则静态检查 + 标记未验证）；
- `-Stage` 列表数量/名称不变（16 个，含可选 desktop）；
- 负样例：`-ArtifactSha256` 不匹配时必须失败，不能继续解压；
- 真实 Windows 安装留 P2 E2E，不在 A6 冒充通过。

### 2.6 `upstream` remote 与月度 re-baseline 纪律

#### 2.6.1 remote 拓扑（A1 修）

| remote | 现在 | 目标 | push |
| --- | --- | --- | --- |
| `origin` | `7067567g/gugu` | 保持 | 允许（人工） |
| `upstream` | `joydove/gugu`（误导） | 改名为 `mirror`（或保留但文档写清“这是 gugu 镜像，不是 Hermes 上游”） | **DISABLED** |
| `hermes` | 不存在 | `https://github.com/NousResearch/hermes-agent.git`，**只读** | **DISABLED**（`git remote set-url --push hermes DISABLED`） |

硬规则：任何脚本/文档不得把 `upstream` 当 Hermes；`upstream.lock` 记录的是
`hermes` remote 的 tag/commit。

#### 2.6.2 月度 cadence（稳定后执行）

| 项 | 规定 |
| --- | --- |
| 触发 | 每月第一个周一；安全类 release 可插队（单独一轮，不等到月度） |
| 跟踪对象 | 只跟 **stable release tag**（`v0.22.x`），不跟 main/canary/rc |
| 覆盖窗口 | 约 8 个 release / 30 天；一次 re-baseline 只做一个 tag |
| 预算 | 单次 **≤2 人日**（含测试/打包 smoke）；超过 3 人日连续两次 → 翻案评审 |
| 固定产出 | ① 文件级三方合并报告；② 符号漂移报告（0 未解析）；③ 测试/编译报告；④ 更新后的 `upstream.lock`；⑤ 新 baseline（仅计数变化时） |
| 分支/合并 | `rebaseline/vX.Y.Z` → 计划评审 → 合 main；不 push 上游 |
| 版本 | `version-sources.json` 的 `upstream_kernel` 组随 tag 更新；`GUGU_VERSION` 只在产品发布时动 |
| 基线 | 只有 A7 测试绿后才 `--update-baseline --reason`；CI 永不执行 update |
| 留痕 | 每个 re-baseline 一个 run 目录 + `VERDICT.md` + `DONE`；跨轮可追溯 |

#### 2.6.3 翻案 A→B（钉版本）的触发条件

沿用 EVAL §7.3，并给出可机械检查的口径：

1. **成本恶化**：单次 re-baseline 冲突 hunk > 60，或连续 2 次 > 3 人日。
2. **接触面扩张**：修改文件 > 40 或新增行 > 1,500；或开始改 top-4 churn 文件。
3. **扩展点承诺破裂**：上游对 `api_version` 破坏性提升 / 移除 `VALID_HOOKS` 的 gugu 依赖 /
   `manifest v1` 不再 supported forever。
4. **语义漂移失控**：单月符号漂移 > 20 个，或“整文件删除”落在 gugu 修改清单内。
5. **节奏失控**：stable release > 2/周，月度覆盖不了安全修复。
6. **测试不可修复**：gugu 测试在新基线上一个周期内无法转绿。

**B 预案（预置，不执行）**：钉 `v0.22.5`（`bf0203a7`），打 tag `gugu-base-v0.22.5`；
`hermes` remote 改只读参考、不再合并；只做 CVE/凭据泄漏类 `cherry-pick -x`，
每条记 `docs/SECURITY-BACKPORTS.md`；每 90 天用同一套脚本复评一次，成本回落可回 A。

### 2.7 轨道 A 的验收、回滚、停止条件

**阶段验收（A8 结束）**：
- [ ] 35/35 内核文件在新 tag 上；0 冲突标记；`py_compile` 全过。
- [ ] gugu 18 个测试文件（3,114 行）绿；`tests/ci` 绿；模型 provider 测试绿。
- [ ] 符号漂移 0 未解析（allowlist 全部有 reason/owner/复核轮）。
- [ ] `arch-gate` 在新基线重 seed 后有 reason、独立 commit、非作者 review；计数下降或持平可解释。
- [ ] `install.ps1` 0 处上游 URL（注释除外）；`-LegacyClone` 可回滚；stage 协议 16 个不变。
- [ ] `upstream.lock` = v0.22.5/bf0203a7；`version-sources` kernel 组 = 0.22.5。
- [ ] git diff 只在允许目录；无业务模块之外的意外改动；工作树干净。
- [ ] 计划评审轮 PASS；下一阶段（P1 或 P2）入口条件满足。

**回滚**：
- 整阶段回滚：`git switch main`，删除/保留 `rebaseline/v0.22.5` 分支；main 不动。
- 单文件回滚：A3–A6 每文件独立 commit，可逐个 revert。
- 安装回滚：`-LegacyClone` 参数。
- 基线回滚：baseline/ledger 的更新是独立 commit，可单独 revert（但计数会回到旧基线，
  需要重新 seed，属显式操作）。

**停止条件（任一触发 → 停，不硬上）**：
1. A2 演练 hunk > 60，或人工估算 > 3 人日。
2. A5 出现“上游整段搬走 gugu 依赖的函数”，且 2 轮内无法重新表达。
3. A7 测试连续 2 轮无法转绿，且失败原因是上游行为变化而非 gugu bug。
4. 新漂移 > 20 个/月，或出现 gugu 修改文件被上游整文件删除。
5. 任何 top-4 churn 文件需要改（触发翻案评审，不是继续）。
6. 用户/发布经理 STOP；或 Windows/网络/凭据不可用导致无法验证关键门。

### 2.8 轨道 A 轮次表

| 轮次 | 轮型 | 级别 | 目标 | 文件边界 | 门禁 | 证据 | 停止条件 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gugu-reb-r01-prep` | architecture/implement | 骨架特化 | A0+A1：manifest、remote、tag、漂移基线 | `arch/upstream.lock`、`scripts/upstream/**`、`reports/` | tag=bf0203a7；drift 0 未解析/allowlist；工作树干净 | `reports/drift-v0.22.5.json`、`reports/rebase-manifest.json` | 漂移 >20 或 tag 不可得 |
| `gugu-reb-r02-rehearsal` | architecture/verify | 骨架特化 | A2：完整三方合并演练 + hunk 分类 | 只读 + `/tmp` scratch；不改目标仓 | 26/26 有结论；hunk 表可复算 | `reports/rebase-rehearsal-full.md` | hunk>60 / >3 人日 |
| `gugu-reb-r03-newfiles` | optimize/implement | 骨架特化 | A3：新分支 + 9 新文件 | `rebaseline/v0.22.5` 分支；9 文件 | py_compile 9/9；无冲突 | `reports/rebase-a3.txt` | 新文件 import 解析失败 |
| `gugu-reb-r04-clean` | optimize/implement | 骨架特化 | A4：12 干净文件落树 | 12 文件 | 每文件 diff 与 manifest 一致；compile | `reports/rebase-a4.txt` | 干净合并结果与演练不一致 |
| `gugu-reb-r05-conflicts` | optimize/implement | 骨架特化 | A5：14 冲突文件按 A/B/C 重放 | 14 文件 | 0 冲突标记；compile；关键测试 | `reports/rebase-a5.md` | 出现整段搬走且 2 轮无法表达 |
| `gugu-reb-r06-drift-install` | optimize/implement | 骨架特化 | A6：漂移收口 + install.ps1 归属 | 漂移文件、`scripts/install.ps1`、`install.sh` | 0 未解析；5 处 URL 清零；16 stage；负样例 sha 不匹配失败 | `reports/rebase-a6.txt` | tirith 语义无法在 2 轮内落定 |
| `gugu-reb-r07-tests` | optimize/implement | 骨架特化 | A7：测试/编译/arch gate 全绿 | 测试文件、必要的产品修复 | 18 测试文件绿；gate 重 seed 有 reason | `reports/rebase-tests.txt` | 连续 2 轮测试不绿 |
| `gugu-reb-r08-merge` | verify/plan | 计划评审 | A8：版本/文档/合并/阶段验收 | 版本文件、docs、merge commit | §2.7 清单全 PASS | `VERDICT.md`、`reports/rebase-closeout.md` | 任一验收项 FAIL |

> **不设总轮数**：A2/A5/A7 都可能追加轮次（如 `r05b`、`r07b`）；追加走 §6 backlog，
> 不改变阶段边界。A1/A2 只读，可与 Phase 2 P1 并行。

---

## 3. 轨道 B — 产线化 P1 / P2 / P3

### 3.1 阶段映射与总原则

| 阶段 | 目标 | 不做什么 | 入口条件 | 出口条件 |
| --- | --- | --- | --- | --- |
| **P1** | 建 `prodline/` 骨架 + 6 类 receipt schema + `accept.py`（先 source/build） | 不改产品代码、不改 install.ps1、不改 pyproject | Phase 0 已收口；`arch/architecture.yaml` 可加 prodline 为 P | `accept.py --only source,build` 在 Linux 产出合法 `acceptance-report.json`；receipt 归属写清 |
| **P2** | `build-win` job + `install.ps1 -FromArtifact` + install/runtime receipt + Windows E2E 接线 | 不重写 install.ps1 工具链安装；不做签名；不改 Tauri/NSIS | P1 的 receipt schema 冻结；A6 的 install 归属修正已落地 | 双构建可复现门通过；干净 Win11 装 artifact → 首启 <5min → 两份 receipt；10/10 有计数 |
| **P3** | 三件发行物：`gugu-kernel`（K）、`gugu-glue`（G）、`gugu-extensions`（X）；P 不进发行物 | 不做全量目录搬家；不做插件市场/进程沙箱/依赖求解器 | P2 的 artifact 安装稳定；K→X = 0（P3a）；pyproject 变更获批准 | 三 wheel 可独立构建/安装；边界断言过；插件发现不回归 |

**总原则**：
1. **规则先于搬家，接线先于重建**（ARCH §6.2）。P1 只加目录与 schema，不改产品。
2. **回执两轨**：产品回执（telemetry）不可当验收证据；产线回执（evidence）只由 P 写。
3. **P 不被产品 import**：`prodline/` 不进 `pyproject` 的 include；arch 账本把 prodline 记 P，
   K/X/G→P 的违规计数必须保持 0。
4. **每阶段一个计划评审轮**：阶段开始前定轮次表，结束后验收/回滚/追加 backlog。

### 3.2 P1 — prodline 骨架与 receipt 归属

#### 3.2.1 目录与职责

```text
prodline/
├── pipeline.yaml                 # 5 段：source/build/install/runtime/quality；stage id 稳定
├── stages/
│   ├── source.py                 # 干净 clone/版本断言/lockfile 哈希
│   ├── build.py                  # 双构建/产物哈希/build-receipt（P2 起接 Windows）
│   ├── install.py                # 装 artifact/首启计时/install+runtime receipt（P2 起）
│   ├── runtime.py                # 20 任务/故障注入/task+recovery receipt（P5 起）
│   └── quality.py                # 72h soak/聚合（P5 起）
├── gates/
│   ├── receipt_check.py          # schema 校验 + sha256 链 + 篡改检测
│   └── arch_boundaries.py        # P1 只读引用现有 arch-gate（不复制规则）
├── receipts/schema/
│   ├── build-receipt.v1.json
│   ├── install-receipt.v1.json
│   ├── runtime-receipt.v1.json
│   ├── task-receipt.v1.json
│   ├── recovery-receipt.v1.json
│   └── release-receipt.v1.json
└── runners/
    ├── accept.py                 # 聚合器：只聚合 receipt，不重新计算
    └── soak.py                   # 72h（P5）
```

#### 3.2.2 receipt 归属（P1 的核心交付）

| receipt | 生产者 | 所有者 | 输入 | 不可信来源（禁止写入） | 证据链 |
| --- | --- | --- | --- | --- | --- |
| `build-receipt/v1` | `stages/build.py` | 发布经理（P） | CI job、双构建产物、lockfile | 产品代码、Agent | 产物 sha256 + job id + commit |
| `install-receipt/v1` | `stages/install.py` | P | installer 日志（产品侧，仅线索）+ P 自己观测 | installer 自报、Agent | 安装路径/版本/耗时/卸载 |
| `runtime-receipt/v1` | `stages/install.py` | P | P 启动后端 + P 的 health 探针 | 壳的 90s/45s、产品自报 | pid/port/ready_seconds/health |
| `task-receipt/v1` | `stages/runtime.py` | P | 20 任务输入/输出/校验 | 产品自评 | input/output hash + verify |
| `recovery-receipt/v1` | `stages/runtime.py` | P | P 注入 kill/断网/429 | 产品自愈日志 | 注入时间/恢复时间/状态丢失/重复副作用 |
| `release-receipt/v1` | `stages/install.py`（升级/回滚） | P | 升级前后 DB 行哈希 | 产品迁移日志 | data_check/rollback_ok |
| 产品回执 `update_receipt.py` | 产品代码 | 产品（K） | 产品自述 | — | **不得**作为 P 的输入结论 |

**三条硬规则**：
1. P 可以**读**产品回执/日志作为线索，但每条结论必须由 P 自己观测（进程、端口、sha256、
   DB 行、HTTP 探针）得出。
2. `acceptance-report.json` 只聚合上述 receipt，不重新计算；聚合器本身是 P，产品不可 import。
3. schema 冻结后只加字段不改语义；任何破坏性变更必须新 `v2` 并与 `v1` 并存一个周期。

#### 3.2.3 P1 的验收与回滚

**验收（P1 出口）**：
- [ ] `prodline/` 目录、`pipeline.yaml`、6 个 schema、`receipt_check.py`、`accept.py` 在盘。
- [ ] `python -m prodline.runners.accept --only source,build --out artifacts/receipts/acceptance-report.json`
      在 Linux 上 exit 0；未实现的段为 `SKIP`（不是 PASS、不是假绿）。
- [ ] `acceptance-report.json` 结构合法：`schema`、`commit`、`stages[]`、`receipts[]`（含 path+sha256）、
      `verdict`；`receipt_check.py` 能对篡改的 receipt 报错（负样例）。
- [ ] `arch/architecture.yaml` 把 `prodline/**` 记为 P；`K->P`/`X->P`/`G->P` 计数仍为 0；
      ledger 更新有 reason、独立 commit。
- [ ] `pyproject` 的 include **不含** `prodline`；`python -c "import prodline"` 在产品安装环境里
      不是必需（产品进程没有 import 路径依赖）。
- [ ] CI 有 `prodline-gate` job（跑 `pytest prodline` + 一次 `accept --only source`），
      无 soft-fail，进 `all-checks-passed.needs`。
- [ ] P1 第一刀顺带清掉 r03 的 F1/F2/F4（见 §6 B1–B3），因为它们同属“门禁硬化”，
      且都在 CI 测试文件里，拖到业务迁移后成本更高。

**回滚**：
- 删 `prodline/` + `artifacts/` + `prodline-gate` job 即可；产品零影响。
- ledger 的 `prodline` 记录与 baseline 更新是独立 commit，可单独 revert。

**P1 轮次表**：

| 轮次 | 轮型 | 级别 | 目标 | 文件边界 | 门禁 | 证据 | 停止条件 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gugu-pl-r01-skeleton` | architecture/implement | 骨架特化 | 目录 + pipeline + 6 schema + 归属文档 + arch ledger P | `prodline/**`、`arch/architecture.yaml`、`docs/` | schema JSON 合法；ledger P；K/X/G→P=0 | `reports/p1-skeleton.md`、ledger diff | 2 轮内目录/所有权定不下来 → 砍到 3 schema |
| `gugu-pl-r02-source` | optimize/implement | 目标特化 | `stages/source.py` + source receipt + 版本断言（含 tauri 漏登） | `prodline/**`、测试 | 负样例：版本漂移必须红；lockfile 哈希可复算 | `reports/p1-source.txt` | 版本断言需要改产品代码 → 停，回计划 |
| `gugu-pl-r03-gate` | optimize/implement | 骨架特化 | `receipt_check.py` + `accept.py` + CI `prodline-gate` + F1/F2/F4 | `prodline/**`、`.github/workflows/ci.yml`、`tests/ci` | 篡改 receipt 红；聚合 SKIP 正确；CI 无 soft-fail | `reports/p1-gate.txt` | CI 改动导致既有 lane 红 |
| `gugu-pl-r04-close` | verify/plan | 计划评审 | P1 独立复核 + 出口验收 | 只读 + 修不一致 | §3.2.3 清单全 PASS | `VERDICT.md`、`reports/p1-closeout.md` | 任一 FAIL |

### 3.3 P2 — 安装从 artifact + Windows build job

#### 3.3.1 CI `build-win`（只 build，不 install）

```yaml
build-win:
  runs-on: windows-latest
  steps:
    - checkout
    - setup-node (pin from .nvmrc)
    - setup-python 3.12
    - npm ci
    - npm run build
    - npm run dist:win:nsis        # 第一次
    - hash + copy artifacts        # build-1
    - clean + build again          # 第二次（同一 commit、同一 runner）
    - compare (sha256 或规范化 manifest)
    - prodline build receipt       # build-receipt/v1
    - upload-artifact
```

设计点：
- **只 build 不 install**：Windows runner 可免费做构建；安装 10/10 需要干净 VM/runner，
  单独 job `install-e2e-win`（先 `workflow_dispatch`，稳定后再 nightly）。
- **双构建**：同 runner、同 commit、清缓存后再 build；比较两档：
  1. 整包 `sha256` 相等（首选）；
  2. 若不相等（electron-builder 时间戳/嵌入日期等），用**规范化 manifest**：
     文件列表排序 + 每文件 sha256 + 文件大小相等；必须在 receipt 里写明用了哪一档，
     **不得只写“sha 一致”而实际比的是 manifest**。
- **receipt**：`build-receipt/v1` 由 `stages/build.py` 生成，含 commit、tag、toolchain、
  lockfile 哈希、artifact 列表（path/sha256/bytes）、`clean_build`、`reproducibility_mode`。
- **Windows 不可得**：若 `windows-latest` 配额/网络不可用，P2 明确写“未验证”，
  不拿 Linux 交叉构建冒充 Windows 产物。

#### 3.3.2 `install.ps1 -FromArtifact`

在 A6 的 `-ArtifactUrl/-ArtifactSha256/-LegacyClone` 基础上补：
1. `-FromArtifact <path|url>`：下载/读取 → 校验 sha256 → 解压到 `$InstallDir` →
   `repository` stage 走“artifact 布局”分支；不 `git clone`、不 `git init`（除非 `-LegacyClone`）。
2. `-InstallReceiptPath`：installer 写**产品侧** `install.log.json`（线索）；P 的 `stages/install.py`
   读取后**自己观测**并生成 `install-receipt/v1`。
3. `-ReadyProbeUrl`：P 用后端 health 探针计时（spawn → 探针成功），不用壳的 90s/45s。
4. `-UninstallCheck`：卸载/回滚路径的退出码与残留检查；结果进 install-receipt。
5. 保留 `-LegacyClone` 一参回滚；保留 15+1 stage 协议；不重写 uv/node/git 安装逻辑。

#### 3.3.3 P2 的验收与回滚

**验收（P2 出口）**：
- [ ] `build-win` job 在真实 `windows-latest` 上绿；两次构建按 §3.3.1 的档位判定通过，
      档位写进 receipt。
- [ ] `build-receipt/v1` 可机读；artifact 上传；commit/tag/lockfile 哈希可复算。
- [ ] `install-e2e-win`（`workflow_dispatch`）跑 `windows-e2e.ps1`：干净 Win11 装 artifact，
      **10/10** 有计数；首启到 P 的 health 探针 **<300s**；install/runtime receipt 各一份。
- [ ] `-ArtifactSha256` 不匹配时安装失败且不留半装状态；`-LegacyClone` 可回退旧路径。
- [ ] 卸载/回滚后数据完整（release-receipt 的 `data_check`），或明确标注“本阶段未覆盖”并给去向。
- [ ] 所有 receipt 的 sha256 链可由 `receipt_check.py` 验证；篡改负样例红。
- [ ] 真实 GitHub Actions 首跑由发布经理/用户推送后核验；本轮/本阶段不 push，不冒充已跑。

**回滚**：
- 移除 `build-win` / `install-e2e-win` job + 删 `build.py`/`install.py` 对应段；artifact 保留。
- install.ps1 的 artifact 分支用 `-LegacyClone` 关闭；不改工具链安装。
- 不影响 P1 的 source/receipt 骨架。

**P2 轮次表**：

| 轮次 | 轮型 | 级别 | 目标 | 文件边界 | 门禁 | 证据 | 停止条件 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gugu-pl-r05-buildwin` | architecture/implement | 骨架特化 | `build-win` job + 双构建 + build-receipt | `.github/workflows/**`、`prodline/stages/build.py`、docs | 双构建档位通过；receipt 合法；无 soft-fail | `reports/p2-buildwin.txt` | 3 次构建仍无法复现且 manifest 也不等 → 停在“未验证” |
| `gugu-pl-r06-artifact-install` | optimize/implement | 骨架特化 | `-FromArtifact` + sha 校验 + install/runtime receipt + readiness | `scripts/install.ps1`、`prodline/stages/install.py`、`tests/install` | sha 负样例红；stage 16 不变；receipt 合法 | `reports/p2-install.txt` | 需要重写工具链安装 → 停 |
| `gugu-pl-r07-e2e` | optimize/implement | 骨架特化 | `install-e2e-win` + windows-e2e 接线 + 10/10 计数 | `.github/workflows/**`、`tests/install/**`、docs | 10/10（真 runner）或明确未验证；首启 <300s | `reports/p2-e2e.txt` | Windows 不可得连续 2 轮 → 降级为 build-only + 文档 |
| `gugu-pl-r08-close` | verify/plan | 计划评审 | P2 独立复核 + 出口验收 | 只读 + 修不一致 | §3.3.3 清单全 PASS/未验证有去向 | `VERDICT.md`、`reports/p2-closeout.md` | 任一 FAIL 且无法一轮修 |

### 3.4 P3 — 打包拆分（内核 / 扩展发行物）

#### 3.4.1 目标发行物

| 发行物 | 内容（K/X/G/P） | 依赖 | 不包含 | 说明 |
| --- | --- | --- | --- | --- |
| `gugu-kernel` | K：`agent/ tools/ providers/ hermes_state*.py hermes_platform/ cron? contracts/` | 仅 K | `plugins/`、`hermes_cli/`、`gateway/`、`apps/`、`prodline/` | `cron` 归 K 还是 G 需在 P3a 用 Q1–Q6 定；默认 K（持有调度状态） |
| `gugu-glue` | G：`hermes_cli/ gateway/ tui_gateway/ acp_adapter/` | K | X、P、桌面壳（壳随桌面发） | 提供 CLI/HTTP/JSON-RPC 宿主 |
| `gugu-extensions` | X：`plugins/ skills/ optional-skills/ optional-mcps/` | K + glue（过渡期）；最终 K + `contracts/` | K 的实现、G 的内部、P | 过渡期允许 X→G；P3a 后逐步收契约 |
| `prodline` | P | 任意（只读） | **不进任何发行物** | 结构上保证 Agent 改不到回执生成器 |

**为什么是三件而不是两件**：goal 说“内核/扩展发行物”，但 `hermes_cli`/`gateway`/`tui_gateway`
既不是 K（不持领域状态）也不能塞进 extensions（它们是宿主）。把 glue 单独成 wheel，
才能让 kernel 保持“无出边”、extensions 声明“依赖 kernel+glue”，而不是把 glue 硬塞进 kernel。

**为什么不是全量目录搬家**：ARCH §7.2 明确不做 `agent/→kernel/`、`apps/→glue/` 的全量搬家
（3,000+ 文件 diff，撞上游同步 R1）。P3 只改**打包边界**（include/entry points/wheel 内容），
不改源码路径；`kernel/` 目录化留给后续 P5，不在本计划承诺。

#### 3.4.2 三步走

**P3a｜K→X 清零（前置）**
- 现状：`tools/ → plugins/` 22 处静态 import（账本 `tools->plugins: 22`），
  违反 K→X。P3a 把这些改成注册表发现（`providers` 式 registry / entry points），
  不改插件实现，只改内核“认识具体插件”的方式。
- 验收：arch gate `K->X = 0`；22 处 import 清零；插件行为不回归（相应测试）；
  baseline 更新有 reason；每个厂商插件仍可被发现。
- 回滚：单 commit revert；旧静态 import 恢复。
- 若 3 轮内无法清零 → 停止 P3，保留单发行物；不把“split”做成半吊子。

**P3b｜wheel 拆分 + entry points**
- `pyproject.toml` 拆成三个发行物（或三个 build profile）：kernel / glue / extensions；
  `prodline` 不在任何 include；`[project.entry-points]` 增加插件发现组；
  `version-sources.json` 登记三件发行物的版本/依赖。
- 验收：`python -m build` 产出三 wheel；`unzip -l` 边界断言（kernel 无 plugins/hermes_cli/gateway/
  apps/prodline；extensions 无 K 实现；prodline 不在任何 wheel）；`pip install` 矩阵
  （kernel-only / kernel+glue / 三件）能 import 冒烟；插件发现不回归；lockfile 更新。
- 回滚：revert `pyproject` + 恢复单 include；已产出的 wheel 不删（可继续装）。
- **门槛**：pyproject 变更必须用户/发布经理显式批准（本计划不授权）；单独 release 窗口，
  不与 re-baseline 同一轮。

**P3c｜安装消费拆分产物**
- `install.ps1` 的 artifact 分支支持“三件套”安装顺序（kernel → glue → extensions），
  校验每件 sha256；`build-receipt` 记录三件；`install-receipt` 记录实际安装组合；
  `version-sources` 的断言覆盖三件。
- 验收：干净 Win11 用三件 artifact 安装 → 首启 → 插件可用 → receipt 三件齐全；
  回滚到单件 artifact 仍可装。
- 回滚：install.ps1 选择单件 artifact 的旧分支；三件产物保留。

#### 3.4.3 P3 的验收与回滚

**验收（P3 出口）**：
- [ ] `K->X = 0`；`K->P/X->P/G->P = 0`；`prodline` 不在任何发行物。
- [ ] 三 wheel 可独立构建；`unzip -l` 边界断言 100% 通过。
- [ ] 安装矩阵：kernel-only 冒烟；三件套在干净 Win11 装成功（或明确未验证 + 去向）。
- [ ] 插件发现不回归：至少 `gugu-design`、`kanban`、一个 provider 插件可加载。
- [ ] `version-sources.json` 覆盖三件发行物；版本断言测试绿。
- [ ] payoff：未来新增一个扩展不需要改 kernel（有 1 个“新插件只装 extensions”演示）。

**回滚**：
- 单 commit revert `pyproject`/entry points；恢复单发行物；三 wheel 产物保留但不再发布。
- 安装回滚用单件 artifact 分支；数据无迁移。
- `contracts/` 若已引入，保留为空目录不发布，或随 revert 删除。

**P3 轮次表**：

| 轮次 | 轮型 | 级别 | 目标 | 文件边界 | 门禁 | 证据 | 停止条件 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gugu-pl-r09-kx-zero` | architecture/implement | 骨架特化 | P3a：22 处 tools→plugins 改注册表 | `tools/**`、`providers/**`、`arch/architecture.yaml`、测试 | K->X=0；插件发现不回归；gate 重 seed 有 reason | `reports/p3a-kx.txt` | 3 轮未清零 → 停 P3 |
| `gugu-pl-r10-wheel-split` | architecture/implement | 骨架特化 | P3b：三发行物 + entry points + 边界断言 | `pyproject.toml`、`setup.py`?、`version-sources.json`、构建脚本 | 三 wheel；unzip 断言；安装矩阵 | `reports/p3b-wheels.txt` | pyproject 未获批准 / 矩阵失败 2 轮 |
| `gugu-pl-r11-install-split` | optimize/implement | 骨架特化 | P3c：install.ps1 装三件套 + receipts | `scripts/install.ps1`、`prodline/**`、`tests/install` | 三件 sha 校验；收据齐全；回滚单件可装 | `reports/p3c-install.txt` | 干净机装三件失败 2 轮 |
| `gugu-pl-r12-close` | verify/plan | 计划评审 | P3 独立复核 + 出口验收 + 下一阶段建议 | 只读 + 修不一致 | §3.4.3 清单全 PASS/未验证有去向 | `VERDICT.md`、`reports/p3-closeout.md` | 任一 FAIL |

### 3.5 产线 backlog（Phase 2–4 相关）

| ID | 类型 | 描述 | owner | 去向 | 最晚复核 |
| --- | --- | --- | --- | --- | --- |
| B1 | 门禁 | r03 F1：`tests/ci/test_ci_workflow_wiring.py` 的 `REQUIRED_JOBS` 未含 `arch-gate` | 门禁 owner | `gugu-pl-r03-gate` | P1 结束 |
| B2 | 门禁 | r03 F2：首方 `parse_errors > 0` 未 gate（建议 exit 2 + 显式 allowlist） | 门禁 owner | `gugu-pl-r03-gate` | P1 结束 |
| B3 | 门禁 | r03 F4：`pytest scripts/arch` 未进 CI（建议 arch-gate 加 step） | 门禁 owner | `gugu-pl-r03-gate` | P1 结束 |
| B4 | 文档 | r02 `VERDICT/DONE` commit 数/HEAD 陈旧（3 commits/9da4f1bb → 4/cbba53a5） | PMO | 下一轮计划评审 | P1 结束 |
| B11 | 事实 | `install.ps1` stage 数 18（ARCH）vs 16（实测） | P2 owner | `gugu-pl-r06-artifact-install` | P2 结束 |
| B12 | 结构 | 就绪定义两处：`backend-ready.ts:22=90s` / `backend-health.ts:1=45s` | P2 owner | P2 的 readiness 收口 | P2 结束 |
| B13 | 结构 | 三个安装器（ps1 / Tauri / NSIS）无主 | 发布经理 | P2 计划评审立项 | P2 结束 |
| B14 | 版本 | `tauri.conf.json:4=0.21.1` 未登记 `version-sources.json` | P1 source owner | `gugu-pl-r02-source` | P1 结束 |
| B15 | 版本 | 5 个版本源 / 3 个仓库 URL 不一致 | 发布经理 | P1 source + P3b | P3 结束 |
| B16 | 接线 | `tests/scripts`、`tests/install/windows-e2e.ps1` 不在任何 workflow | P2 owner | `gugu-pl-r07-e2e` | P2 结束 |
| B17 | 契约 | 106 plugin.yaml / 3 dashboard manifest / 4 前端插件，三 schema 无一致性断言；0 `api_version`/`health` | P3 owner | P3 之后单独立项 | P3 结束 |
| B18 | 回执 | `update_receipt.py`（产品）与 P 回执两轨未文档化 | P1 owner | `gugu-pl-r01-skeleton` | P1 结束 |
| B19 | 资产 | `artifacts/` 不存在；`acceptance-report` 无生产者 | P1 owner | `gugu-pl-r01-skeleton` | P1 结束 |
| B20 | 打包 | 单 include 收所有包；无 `[project.entry-points]` | P3 owner | `gugu-pl-r10-wheel-split` | P3 结束 |
| B21 | 环境 | Windows runner/VM 配额与网络未验证 | 发布经理 | P2/P3 每轮入口检查 | 持续 |
| B22 | 构建 | electron-builder 产物确定性未验证（时间戳/嵌入日期） | P2 owner | `gugu-pl-r05-buildwin` | P2 结束 |
| B23 | 测试 | 72h soak / 20 任务集 / 故障注入 harness 未建 | P5 owner | Phase 5 立项 | P3 结束 |
| B24 | 测试 | 4,735 个测试文件 vs CI 只跑 14+2 组 | 门禁 owner | P5 立项（不与 P1–P3 混） | P3 结束 |
| B25 | 上限 | 9 个新文件未纳入 26/774 的“修改文件”额度，需单列总面 | 计划 owner | 本计划 §2.2 | Phase 1 结束 |
| B26 | 门禁 | module pair 只定位不 gate（r03 F8） | 门禁 owner | 下一轮计划评审 | P1 结束 |
| B27 | 基线 | re-baseline 后 `arch/baseline.json` 必须重 seed（计数变化） | Phase 1 owner | A7/A8 | Phase 1 结束 |
| B28 | 环境 | Python 3.11 实跑 gate 未做 | 门禁 owner | Phase 1 A7 | Phase 1 结束 |
| B29 | CI | 真实 GitHub Actions 首跑（本机无 runner、未 push） | 发布经理 | P2 首次推送后 | P2 结束 |
| B30 | 发布 | push/tag/publish 授权与窗口 | 用户 | 独立发布轮 | 持续 |

---

## 4. 工作流适配：目标特化 / 骨架特化 / 计划评审

### 4.1 三级判据（机械可判）

| 级别 | 现行名 | 原标签（历史） | 判据（满足任一） | 默认产物 | 典型轮 |
| --- | --- | --- | --- | --- | --- |
| **L1** | 目标特化 | L1 目标特化 | 只换 goal/inputs/outputs，骨架不变；改动 ≤3 个文件；无新门禁/新目录；单 commit 可回滚 | 代码/文档小改 + 既有门禁 | 版本断言、文档修正、单文件漂移、verify 轮 |
| **L2** | 骨架特化 | L2 骨架特化 | 需要专门 WS 切分/新门禁/新证据格式/多阶段；新增目录、CI job、安装路径、打包结构；或 >10 文件 | ROUND-PLAN + WS 表 + 新门禁 + 回滚点 | re-baseline 重放、prodline 骨架、build-win、artifact 安装、wheel 拆分 |
| **L3** | 计划评审 | L3 计划评审 | 只产出计划/判词/验收，不改产品；阶段开始/结束，或不可逆步骤前（合并 main、切 wheel、切 artifact） | 计划/VERDICT + 门禁清单 + backlog | 本计划轮、每阶段 close、A8 前、P3b 前 |

**判定顺序**：
1. 只改 docs/plan/verdict → **计划评审**。
2. 否则若新增目录/CI job/打包结构/安装路径，或 >10 文件 → **骨架特化**。
3. 否则 → **目标特化**。

**反例**：
- “只改一个 CI job”仍是骨架特化——CI job 是新门禁/新证据面。
- “只改 pyproject 一行”是骨架特化——发行物边界变了。
- “只改 install.ps1 一个参数”若改变安装来源，是骨架特化；只改文案是目标特化。

### 4.2 总轮次表（Phase 0–5）

> 每轮 ≤3 目标、≤6500s 收尾；文件边界互斥；门禁/证据/停止条件见对应阶段。
> `原标签` 列仅作历史对照；现行名以 §4.1 为准。

| Phase | run-id | 轮型 | 级别 | 原标签 | 目标（一句话） | 文件边界 | 门禁 | 证据 | 停止条件 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | `gugu-arch-r01-gate` | implement | L2 | L2 | 分类账本 + checker + baseline | `arch/`、`scripts/arch/` | gate 可跑；自测过 | r01 reports | **已完成** |
| 0 | `gugu-arch-r02-ci` | implement | L2 | L2 | arch-gate 进 CI + 规则文档 | CI + docs | YAML/job 图/无 soft-fail | r02 reports | **已完成** |
| 0 | `gugu-arch-r03-verify` | verify | L3 | L3 | 独立复核 + P0 收口 | 只读 | 正/负样例 + 边界 | r03 VERDICT | **已完成（WARN/CLOSE）** |
| 0 | `gugu-plan-longline` | plan | L3 | L3 | 本长线计划 | `docs/architecture/prodline/**` + run | 文档门 + 事实复跑 | 本 run | 本轮 |
| 1 | `gugu-reb-r01-prep` | architecture | L2 | L2 | manifest/remote/tag/漂移基线 | `arch/upstream.lock`、`scripts/upstream/**` | tag 精确；drift 0 未解析 | `reports/drift-v0.22.5.json` | 漂移>20 |
| 1 | `gugu-reb-r02-rehearsal` | verify | L2 | L2 | 完整三方合并演练 + hunk 分类 | 只读 + scratch | 26/26 结论；hunk 可复算 | `reports/rebase-rehearsal-full.md` | hunk>60 / >3 人日 |
| 1 | `gugu-reb-r03-newfiles` | optimize | L2 | L2 | 9 新文件落新分支 | 9 文件 | compile 9/9 | `reports/rebase-a3.txt` | import 失败 |
| 1 | `gugu-reb-r04-clean` | optimize | L2 | L2 | 12 干净文件落树 | 12 文件 | diff 与 manifest 一致 | `reports/rebase-a4.txt` | 与演练不一致 |
| 1 | `gugu-reb-r05-conflicts` | optimize | L2 | L2 | 14 冲突文件重放 | 14 文件 | 0 冲突标记；compile | `reports/rebase-a5.md` | 整段搬走 2 轮无解 |
| 1 | `gugu-reb-r06-drift-install` | optimize | L2 | L2 | 漂移收口 + install 归属 | 漂移文件 + `install.ps1` | 0 未解析；URL 清零；16 stage | `reports/rebase-a6.txt` | tirith 2 轮未定 |
| 1 | `gugu-reb-r07-tests` | optimize | L2 | L2 | 测试/编译/arch gate 全绿 | 测试 + 必要修复 | 18 测试文件绿；gate 重 seed | `reports/rebase-tests.txt` | 连续 2 轮不绿 |
| 1 | `gugu-reb-r08-merge` | verify/plan | L3 | L3 | A8 版本/文档/合并/阶段验收 | 版本+docs+merge | §2.7 清单 | `reports/rebase-closeout.md` | 任一 FAIL |
| 2 | `gugu-pl-r01-skeleton` | architecture | L2 | L2 | prodline 骨架 + receipt 归属 | `prodline/**`、`arch/`、docs | schema/ledger/P 不被 import | `reports/p1-skeleton.md` | 2 轮定不下 → 3 schema |
| 2 | `gugu-pl-r02-source` | optimize | L1 | L1 | source stage + 版本断言 | `prodline/**`、tests | 负样例版本漂移红 | `reports/p1-source.txt` | 需改产品 → 停 |
| 2 | `gugu-pl-r03-gate` | optimize | L2 | L2 | receipt 门 + accept + CI + F1/F2/F4 | `prodline/**`、CI、`tests/ci` | 篡改红；CI 无 soft-fail | `reports/p1-gate.txt` | 既有 lane 红 |
| 2 | `gugu-pl-r04-close` | verify/plan | L3 | L3 | P1 独立复核 | 只读 | §3.2.3 | `reports/p1-closeout.md` | 任一 FAIL |
| 3 | `gugu-pl-r05-buildwin` | architecture | L2 | L2 | build-win + 双构建 + build-receipt | CI、`prodline/stages/build.py` | 双构建档位；receipt | `reports/p2-buildwin.txt` | 3 次不可复现且 manifest 不等 |
| 3 | `gugu-pl-r06-artifact-install` | optimize | L2 | L2 | -FromArtifact + sha + receipts | `install.ps1`、`stages/install.py` | sha 负样例；16 stage | `reports/p2-install.txt` | 需重写工具链 → 停 |
| 3 | `gugu-pl-r07-e2e` | optimize | L2 | L2 | install-e2e + 10/10 + readiness | CI、`tests/install` | 10/10 或未验证；<300s | `reports/p2-e2e.txt` | Windows 不可得 2 轮 |
| 3 | `gugu-pl-r08-close` | verify/plan | L3 | L3 | P2 独立复核 | 只读 | §3.3.3 | `reports/p2-closeout.md` | 任一 FAIL |
| 4 | `gugu-pl-r09-kx-zero` | architecture | L2 | L2 | 22 处 K→X 清零 | `tools/**`、`providers/**`、arch | K->X=0；插件不回归 | `reports/p3a-kx.txt` | 3 轮未清零 → 停 P3 |
| 4 | `gugu-pl-r10-wheel-split` | architecture | L2 | L2 | 三发行物 + entry points | `pyproject.toml`、version-sources | 三 wheel；unzip 断言 | `reports/p3b-wheels.txt` | 未批准/矩阵 2 轮失败 |
| 4 | `gugu-pl-r11-install-split` | optimize | L2 | L2 | 安装三件套 + receipts | `install.ps1`、prodline | 三件 sha；回滚单件 | `reports/p3c-install.txt` | 干净机 2 轮失败 |
| 4 | `gugu-pl-r12-close` | verify/plan | L3 | L3 | P3 独立复核 + 下一阶段 | 只读 | §3.4.3 | `reports/p3-closeout.md` | 任一 FAIL |
| 5 | `gugu-cad-rNN` | audit/plan | L3 | L3 | 月度 re-baseline + 全链验收 | 按 backlog | §2.6 + campaign 10 条 | 月度报告 | 翻案条件/用户 STOP |

> **追加轮次**：任何阶段出现 `rXXb` 都是允许的；规则见 §0.4。表里不预留“总轮数”，
> 只预留“阶段出口”。

### 4.3 门禁矩阵

| Gate | 作用 | 命令/判据 | 谁跑 | 失败语义 | 证据 |
| --- | --- | --- | --- | --- | --- |
| **G-ARCH** | K/X/G/P import 边界 | `python scripts/arch/check_boundaries.py --baseline arch/baseline.json` | 每轮 CI + 本地 | exit≠0 红；不允许 `--update-baseline` 在 CI | `arch-receipt` / gate 输出 |
| **G-DRIFT** | 符号漂移 | `python scripts/upstream/check_symbol_drift.py --tag v0.22.5` | A1/A6 + 月度 | 0 未解析；allowlist 有 reason | `symbol-drift.json` |
| **G-COMPILE** | 语法/导入冒烟 | `python -m py_compile`（35 内核文件 + 新文件） | 每轮 | 任一失败红 | `reports/compile.txt` |
| **G-TEST** | gugu 测试 | `pytest tests/`（18 文件 3,114 行）+ `tests/ci` + model provider | A7、P1+ | 绿；skip 有 reason | `reports/pytest-*.txt` |
| **G-VERSION** | 版本断言 | `pytest tests/ci` 的版本一致性 + `version-sources.json` | P1 source / A8 | 无 fallback 漂移 | `reports/version.txt` |
| **G-BUILD** | 双构建 | `build-win` job；sha256 或规范化 manifest | P2 | 档位通过；档位写进 receipt | `build-receipt/v1` |
| **G-INSTALL** | 干净机安装 | `windows-e2e.ps1` + 10/10 | P2 | 10/10；否则“未验证” | `install-receipt/v1` |
| **G-READY** | 首启 | P 的 health 探针 spawn→ok | P2 | <300s | `runtime-receipt/v1` |
| **G-RECEIPT** | 回执链 | `receipt_check.py`：schema + sha256 + 篡改 | P1+ | 篡改/缺字段红 | `acceptance-report.json` |
| **G-SPLIT** | 发行物边界 | `unzip -l` 断言 + 安装矩阵 | P3 | 任一越界红 | `reports/p3b-wheels.txt` |
| **G-BOUNDARY** | diff 边界 | `git diff --name-status BASE..HEAD` 只允许目录 | 每轮 | 越界红 | `reports/git-diff-stat.txt` |
| **G-SECRETS** | 凭据 | 正则扫 diff/报告；凭据只走 env | 每轮 | 命中红 | `reports/secrets-check.txt` |

### 4.4 证据标准（所有轮次）

每轮 `reports/` 至少包含：
1. **改动清单**：commit、文件、+/- 行、边界声明。
2. **命令原文 + 输出尾部 + exit code**：不能只写“测试通过”。
3. **diff stat**：`git diff $(cat gugu-arch-pipeline/BASE-COMMIT) --stat`（目标仓）。
4. **未验证项**：显式列出，带原因与去向（最晚复核轮）。
5. **回滚点**：commit/分支/参数，写明怎么回。
6. receipt 类证据：schema、sha256、生产者、时间；不接受“自评 PASS”。

### 4.5 停止条件

#### 4.5.1 单轮
- 6500s 到 → 收尾；先保“能编译 + 测试绿 + 报告 + VERDICT”，剩余进 backlog。
- 同一门禁修 3 轮无进展 → 停，写 VERDICT，交计划评审。
- 预算/凭据/网络不可用 → 停，不把未验证写成通过。

#### 4.5.2 阶段
| 阶段 | 停止条件（任一） | 动作 |
| --- | --- | --- |
| Phase 1 | hunk>60；>3 人日；测试连续 2 轮不绿；漂移>20/月；gugu 修改文件被整删；top-4 churn 被触碰 | 停 re-baseline；评估 A→B；写 VERDICT/backlog |
| Phase 2 | 2 轮内 schema/所有权定不下；accept.py 跑不通；receipt 可被产品改写 | 砍到 3 schema + source-only；或停 |
| Phase 3 | Windows runner 连续 2 轮不可得；双构建 3 次不可复现且 manifest 不等；10/10 连续 2 轮失败 | 降级 build-only + 文档；或停 P2 |
| Phase 4 | K->X 3 轮未清零；wheel 矩阵 2 轮失败；插件发现回归 | 停 P3，保留单发行物 |
| Campaign | 10 条验收连续 2 次同一 P0 失败；用户 STOP/PAUSED | 停；复盘；不硬跑 |

#### 4.5.3 翻案 A→B
见 §2.6.3；触发即由计划评审轮写 `decision: ADJUST`，不得在实现轮里“顺手改方向”。

### 4.6 两轨并行与写冲突

| 文件/目录 | 唯一写者 | 允许并行的阶段 | 冲突处置 |
| --- | --- | --- | --- |
| `agent/ tools/ hermes_cli/ gateway/ tui_gateway/` | Phase 1（A3–A8） | 仅 Phase 1 | 其它轨只读 |
| `scripts/install.ps1` / `install.sh` | A6 → P2 串行 | A6 完成前 P2 不写 | 谁先谁后写进 ROUND-PLAN |
| `pyproject.toml` / `setup.py` / `uv.lock` | P3b 单写者 | Phase 4 | 需显式批准；其它轮禁写 |
| `arch/architecture.yaml` / `arch/baseline.json` | 计划评审轮指定单写者 | P1 加 P / A7 重 seed | 一次只允许一个 commit 改；改前后各跑 gate |
| `.github/workflows/**` | P1（prodline-gate）→ P2（build-win）→ P3 | P1/P2 | 一次只加一个 job；聚合 needs 同轮改 |
| `prodline/**` | Phase 2–4 | 与 Phase 1 可并行 | 只写新目录 |
| `docs/**` | 任意，但同一文件单写者 | 全部 | 文档冲突用追加，不覆盖 |

**并行判据**：两轨同时跑，必须满足“文件集合交集为空”且“共享门禁不互斥”。
否则串行——串行不是浪费，是避免回滚时互相踩。

---

## 5. 阶段验收与回滚总表

| 阶段 | 验收（出口） | 回滚点 | 回滚影响 | 证据 |
| --- | --- | --- | --- | --- |
| Phase 0 | 已完成（r03 WARN/CLOSE） | r01–r03 各自 commit | 无 | `runs/gugu-arch-r03-verify/VERDICT.md` |
| Phase 1 | §2.7 清单 | `rebaseline/v0.22.5` 分支 / 单文件 commit / `-LegacyClone` | main 不动；分支可弃 | `reports/rebase-closeout.md` |
| Phase 2 P1 | §3.2.3 清单 | 删 `prodline/` + CI job | 产品零影响 | `reports/p1-closeout.md` |
| Phase 3 P2 | §3.3.3 清单 | 移除 job + `-LegacyClone` | 旧安装路径仍在 | `reports/p2-closeout.md` |
| Phase 4 P3 | §3.4.3 清单 | revert pyproject + 单件 artifact | 三 wheel 保留但不发布 | `reports/p3-closeout.md` |
| Phase 5 | campaign 10 条 | 分段 SKIP | 不影响已发布产物 | 月度报告 |

---

## 6. Backlog（本轮发现，跨阶段）

> 规则：新增项必须有 `ID + 描述 + owner + 去向 + 最晚复核轮`；没有这五项不得开工。
> 类型：结构 / 上游 / 产线 / 门禁 / 文档。

| ID | 类型 | 描述 | owner | 去向 | 最晚复核 |
| --- | --- | --- | --- | --- | --- |
| B1 | 门禁 | r03 F1：CI wiring 测试未钉 `arch-gate` | 门禁 | `gugu-pl-r03-gate` | P1 结束 |
| B2 | 门禁 | r03 F2：`parse_errors` 非致命 | 门禁 | `gugu-pl-r03-gate` | P1 结束 |
| B3 | 门禁 | r03 F4：`pytest scripts/arch` 未进 CI | 门禁 | `gugu-pl-r03-gate` | P1 结束 |
| B4 | 文档 | r02 VERDICT/DONE commit 数陈旧 | PMO | 下一轮计划评审 | P1 结束 |
| B5 | 上游 | `upstream` remote 指向 `joydove/gugu`，不是 Hermes | Phase 1 | `gugu-reb-r01-prep` | Phase 1 开始 |
| B6 | 上游 | fork 是 root commit、无共享历史；re-baseline 不能 merge | Phase 1 | 本计划 §1.3/§2.4 | Phase 1 开始 |
| B7 | 上游 | 符号漂移检查未产品化；4 是下限，探针 15 候选 | Phase 1 | `gugu-reb-r01-prep` | Phase 1 开始 |
| B8 | 上游 | tirith 退场的语义决策（fail-closed 重表达） | Phase 1 | `gugu-reb-r06-drift-install` | Phase 1 结束 |
| B9 | 上游 | `warn_once`×6 等 6 个候选漂移 | Phase 1 | `gugu-reb-r06-drift-install` | Phase 1 结束 |
| B10 | 上游 | `install.ps1` 5 处 + `install.sh` 2 处 + `windows-e2e.ps1` 2 处上游 URL | Phase 1/P2 | A6 + P2 | P2 结束 |
| B11 | 事实 | stage 数 18（ARCH）vs 16（实测） | P2 | `gugu-pl-r06-artifact-install` | P2 结束 |
| B12 | 结构 | 就绪 90s/45s 两个定义 | P2 | P2 readiness | P2 结束 |
| B13 | 结构 | 三个安装器无主（ps1/Tauri/NSIS） | 发布经理 | P2 计划评审 | P2 结束 |
| B14 | 版本 | `tauri.conf.json:4=0.21.1` 未登记 | P1 | `gugu-pl-r02-source` | P1 结束 |
| B15 | 版本 | 5 版本源 / 3 仓库 URL | 发布经理 | P1 + P3b | P3 结束 |
| B16 | 接线 | `tests/scripts`、`windows-e2e.ps1` 不在 workflow | P2 | `gugu-pl-r07-e2e` | P2 结束 |
| B17 | 契约 | 106 plugin.yaml / 3 manifest / 4 前端，三 schema 无一致性断言 | P3 | P3 后立项 | P3 结束 |
| B18 | 回执 | `update_receipt.py`（产品）与 P 回执两轨未文档化 | P1 | `gugu-pl-r01-skeleton` | P1 结束 |
| B19 | 资产 | `artifacts/` 不存在；acceptance-report 无生产者 | P1 | `gugu-pl-r01-skeleton` | P1 结束 |
| B20 | 打包 | 单 include；无 entry points | P3 | `gugu-pl-r10-wheel-split` | P3 结束 |
| B21 | 环境 | Windows runner/VM 配额未验证 | 发布经理 | P2/P3 入口检查 | 持续 |
| B22 | 构建 | electron-builder 产物确定性未验证 | P2 | `gugu-pl-r05-buildwin` | P2 结束 |
| B23 | 测试 | 72h soak / 20 任务 / 故障注入未建 | P5 | Phase 5 立项 | P3 结束 |
| B24 | 测试 | 4,735 测试文件 vs CI 14+2 组 | 门禁 | Phase 5 立项 | P3 结束 |
| B25 | 上限 | 9 新文件未纳入 26/774 额度，需单列总面 | 计划 | 本计划 §2.2 | Phase 1 结束 |
| B26 | 门禁 | module pair 只定位不 gate（r03 F8） | 门禁 | 下一轮计划评审 | P1 结束 |
| B27 | 基线 | re-baseline 后 baseline 必须重 seed | Phase 1 | A7/A8 | Phase 1 结束 |
| B28 | 环境 | Python 3.11 实跑 gate 未做 | 门禁 | A7 | Phase 1 结束 |
| B29 | CI | 真实 Actions 首跑（本机无 runner、未 push） | 发布经理 | P2 推送后 | P2 结束 |
| B30 | 发布 | push/tag/publish 授权与窗口 | 用户 | 独立发布轮 | 持续 |
| B31 | 上游 | v0.22.5 的 `hermes_cli/plugin_compat.py` 已无 `warn_once`；上游后继未定位 | Phase 1 | A1/A6 | Phase 1 结束 |
| B32 | 上游 | 干净 clone 上的 A2 独立复跑环境未固定（本轮已在 partial clone 上完成 26/26，blob 已补齐） | 环境 owner | A2 入口检查 | Phase 1 开始 |
| B33 | 产线 | `prodline` 未进 `arch/architecture.yaml`（P 类），加它需要 ledger 重 seed | P1 | `gugu-pl-r01-skeleton` | P1 结束 |
| B34 | 产线 | `accept.py` 的 SKIP 语义未定义（SKIP≠PASS） | P1 | `gugu-pl-r01-skeleton` | P1 结束 |

---

## 7. Campaign 状态

> 合同文件：`strategy/campaigns/gugu-prodline-win-0.8.md`（**不在本轮写白名单**）。
> 本轮状态落 `runs/gugu-plan-longline/CAMPAIGN-STATUS.md`；PMO 在下一轮回写合同文件（D-010）。

```yaml
id: gugu-prodline-win-0.8
status: active              # draft → active（本计划立项）
owner: MAIN
baseline: gugu v0.8.0 / 6737fff0
upstream_target: v0.22.5 / bf0203a7
phase: 1-core-alignment     # Phase 0 done; Phase 1 未开始
next_review_round: gugu-reb-r01-prep
progress:
  P0_arch_gate: done (r03 CLOSE/WARN)
  core_alignment: 0%
  prodline_p1: 0%
  prodline_p2: 0%
  prodline_p3: 0%
acceptance_10: 0/10 measured (不得写成通过)
```

### 7.1 阶段进度

| 阶段 | 状态 | 证据 | 下一轮 |
| --- | --- | --- | --- |
| P0 分类/门禁 | **done**（WARN/CLOSE） | `runs/gugu-arch-r03-verify/VERDICT.md` | — |
| Phase 1 re-baseline | planned | 本文件 §2 | `gugu-reb-r01-prep` |
| Phase 2 P1 | planned | §3.2 | 依赖 Phase 1 A1/A2 可并行 |
| Phase 3 P2 | planned | §3.3 | 依赖 A6 + P1 |
| Phase 4 P3 | planned | §3.4 | 依赖 P2 + 批准 |
| Phase 5 cadence | open | §2.6 | 按阶段追加 |

### 7.2 与 campaign 10 条验收的映射

| # | campaign 验收 | 本计划挂载 | 最早可测阶段 | 现状 |
| --- | --- | --- | --- | --- |
| 1 | 干净机 10/10 | P2 `install-e2e-win` | Phase 3 | 未验证 |
| 2 | 首启 <5min | P2 P 的 health 探针 | Phase 3 | 未验证 |
| 3 | 20 任务 ≥95% | Phase 5 runtime stage | Phase 5 | 未建 |
| 4 | 72h 无干预 | Phase 5 soak | Phase 5 | 未建 |
| 5 | kill×3 全恢复 | Phase 5 recovery | Phase 5 | 未建 |
| 6 | 重复副作用 0 | Phase 5 runtime | Phase 5 | 未建 |
| 7 | 日成本 ≤预算 | Phase 5 runtime | Phase 5 | 未建 |
| 8 | 升级/回滚数据完整 | P2/P3 release receipt | Phase 3/4 | 未验证 |
| 9 | 日志/指标/备份/告警 | Phase 5 quality | Phase 5 | 未建 |
| 10 | acceptance-report | P1 聚合器 | Phase 2 | 未建 |

---

## 8. 未验证项（不得写成通过）

| ID | 未验证 | 原因 | 去向 |
| --- | --- | --- | --- |
| U1 | 干净 clone 上的 A2 独立复跑（本轮已在单机 partial clone 上完成 26/26、16 hunk） | 单机结果需第二环境复现 | A2 |
| U2 | 136 处 import 的符号存在性 | tag 侧模块 blob 仍缺失 | A1 补全 blob 后完整扫描 |
| U3 | gugu 18 测试文件在新基线上的结果 | 未合并，不能跑 | A7 |
| U4 | `install.ps1` 的 PowerShell 语法/行为 | 本机无 Windows/pwsh | A6 静态 + P2 E2E |
| U5 | Windows 干净机 10/10 / 首启 <5min | 无 VM/runner | P2 E2E |
| U6 | 双构建整包 sha256 一致性 | 未跑 electron-builder | P2 `build-win` |
| U7 | 三 wheel 拆分可行性 / 安装矩阵 | 未改 pyproject | P3b |
| U8 | 72h soak / 20 任务集 / 故障注入 | 未建 | Phase 5 |
| U9 | 真实 GitHub Actions 首跑 | 未 push | 发布经理 |
| U10 | Python 3.11 实跑 arch gate | 本机无 3.11 | A7 / 门禁 owner |
| U11 | `windows-e2e.ps1` 在 gugu 语境可用性 | 脚本引用上游 CI run；未跑 | P2 |
| U12 | electron-builder 产物确定性 | 未构建 | P2 |
| U13 | `cron/` 归 K 还是 G | 未用 Q1–Q6 判定 | P3a |

---

## 9. 附录

### 9.1 复跑命令（事实 / 演练）

```bash
# 基线/栈
cd /home/a7067567g/下载/deepseek/60-外部仓库/gugu-work
git rev-parse --short HEAD
git diff --numstat 171fdfd7..HEAD -- agent tools hermes_cli gateway tui_gateway \
  | awk '{a+=$1;d+=$2;n++} END{print n,a,d}'
git diff --name-status 171fdfd7..HEAD -- agent tools hermes_cli gateway tui_gateway \
  | awk '$1=="A"{a++} $1=="M"{m++} END{print "A="a,"M="m}'

# 上游 tag
git -C /home/a7067567g/下载/deepseek/60-外部仓库/hermes-upstream \
  rev-parse v0.22.5^{commit}     # bf0203a7…

# 三方合并（单文件；完整脚本见 reports/rebaseline-rehearsal.txt）
git -C <gugu> cat-file blob <base-blob>  > /tmp/b
git -C <up>   cat-file blob <tag-blob>   > /tmp/o
git -C <gugu> cat-file blob <head-blob>  > /tmp/t
git merge-file -p --diff3 /tmp/o /tmp/b /tmp/t | grep -c '^<<<<<<<'
```

### 9.2 演练脚本要点（本轮用的只读方法）

1. `git ls-tree -r <rev>` 建 `path -> blob sha` 映射（不需要下载 blob）。
2. 用 `GIT_NO_LAZY_FETCH=1 git cat-file -e <sha>` 判断 blob 是否本地可用；
   避免触发 promisor 远端拉取。
3. 对可用文件写 `/tmp/{o,b,t}`，跑 `git merge-file -p --diff3`；`rc=0` 干净，
   `rc>0` 冲突，`stdout.count("<<<<<<<")` 为 hunk 数。
4. 对 clean 的 `.py` 用 `compile()` 做语法冒烟；冲突文件不冒充可编译。
5. 本轮已在 26/26 的 tag blob 上完成完整演练（12 干净 / 14 冲突 / 16 hunk），
   与 EVAL 的 main 口径一致；A2 在干净 clone 复跑即可，不再有“8 文件未验证”的缺口。

### 9.3 参考

- EVAL：`/home/a7067567g/下载/deepseek/月月bot/gugu-upstream-eval-20261025.md`
- ARCH：`/home/a7067567g/下载/deepseek/月月bot/gugu-prodline-arch-plan-20261025.md`
- 分类法：`docs/architecture/classification.md`
- 账本/基线：`arch/architecture.yaml`、`arch/baseline.json`
- 上一轮：`runs/gugu-arch-r03-verify/VERDICT.md`、`reports/`
- campaign：`strategy/campaigns/gugu-prodline-win-0.8.md`

### 9.4 版本与修订

| 版本 | 日期 | 变更 | 作者 |
| --- | --- | --- | --- |
| v1 | 2026-10-25 | 初版：三块 + 轮次表 + backlog + campaign 状态 | MAIN（plan 轮） |
