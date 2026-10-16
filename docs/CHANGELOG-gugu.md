# gugu 自有版本 CHANGELOG

> 本文件记录 **gugu 自有版本**的演进，与上游底座 **hermes-agent** 的版本**解耦**：
> `GUGU_VERSION`（本仓根）是 gugu 的语义化版本；`pyproject.toml` 的 `version = "0.21.5"`
> 与桌面包 `apps/desktop/package.json` 的 `0.17.6` 是上游内核/桌面包版本，**不随 gugu 版本递增**。
> 发布 tag 形如 `gugu-vX.Y.Z`，只推 `origin`（fork），**绝不** push `upstream`。

## 0.1.0 — 2026-10-17

- **gugu 版本**：`0.1.0`（本仓首建；首个语义化版本，写入根 `GUGU_VERSION`）
- **上游底座**：hermes-agent `0.21.5`（`pyproject.toml`，未改动）；桌面包 `0.17.6`（未改动）
- **轮型 / run**：优化轮 `gugu-c2-o1`（14 轮流水线第 9/14 轮）

### 目标

| 目标 | 主题 | 摘要 |
| --- | --- | --- |
| `T-02` | 统一 fail-closed 审批结果集 + 审计事件对 | 关闭结果集 `allowed-once \| rejected \| cancelled \| unavailable`；统一 `request(req) -> outcome` 门面；异常路径一律 deny（`unavailable`）；审批决定落 `approval/asked` + `approval/decided`（log-only，不进模型上下文） |
| `T-01` | 子代理委派：面向 session 的持久只读目录 | 在既有持久账本 `async_delegations` 上提供 session 只读列表 API，进程/会话重启后仍能回答「本 session 派过谁」；只读、不触发恢复、不改账本语义 |
| `T-03` | 计划模式持久状态 + `exit_plan_mode` | `/plan` 升级为会话级持久 `active/pending`（`state.db` 独立小表，log-only）；`/plan off` 真正清除；常驻 `exit_plan_mode` 工具校验 `#` 开头的 markdown 计划并交付审查；resume/reload 后状态仍有效 |

### 改动文件清单

实施（WS-O1/O2/O3，均先红后绿）：

- `T-02`（WS-O1）
  - 新增 `tools/approval_outcome.py`
  - 新增 `tests/tools/test_approval_outcome_failclosed.py`
  - 修改 `tools/approval.py`
- `T-01`（WS-O2）
  - 新增 `tools/delegate_catalog.py`
  - 新增 `tests/tools/test_delegate_registry_catalog.py`
  - 修改 `tools/delegate_tool_registry.py`
- `T-03`（WS-O3）
  - 新增 `agent/plan_mode.py`
  - 新增 `tools/plan_mode_tool.py`
  - 新增 `tests/agent/test_plan_mode_state.py`
  - 修改 `toolsets.py`、`hermes_cli/commands.py`、`hermes_cli/cli_commands_mixin.py`、`gateway/run_inbound.py`、`tui_gateway/methods_tools.py`

发布件（WS-V）：

- 新增 `GUGU_VERSION`（`0.1.0`）
- 新增 `docs/CHANGELOG-gugu.md`（本文件）
- 修改 `README.md`（仅新增「## 版本」对照表章节）

### 门禁证据（复跑口径）

统一在暂存克隆 `$W = runs/gugu-c2-o1/.scratch/gugu-work`，解释器 `$W/.venv/bin/python`（Python 3.12.15）：

| # | 命令 | 退出码 | P/F/S | 与基线对比 |
| --- | --- | --- | --- | --- |
| 1 | `.venv/bin/python -m compileall -q tools agent hermes_cli acp_adapter` | 0 | —（1 个 `SyntaxWarning`：`hermes_cli/uninstall.py:265`，既有、与本次无关） | 与基线一致（0） |
| 2 | `HERMES_HOME=$(mktemp -d) .venv/bin/python -m pytest tests/ci -q --noconftest -p no:cacheprovider` | 1 | 120 passed / 4 failed | 与基线一致（120P/4F，4 条为缺 `.github/**` 工作流文件的环境基线） |
| 3 | `HERMES_HOME=$(mktemp -d) .venv/bin/python -m pytest tests/tools/test_approval_outcome_failclosed.py tests/tools/test_delegate_registry_catalog.py tests/agent/test_plan_mode_state.py -q --noconftest -p no:cacheprovider` | 0 | 49 passed / 0 failed | 三目标新测试全绿（30+8+11），另 `test_plan_prompt.py` 2 passed → 合计 51 |
| 4 | 回归：`tests/tools/test_approval.py`（`--noconftest`）；`tests/tools/test_delegate.py tests/tools/test_async_delegation.py`；`tests/agent/test_plan_prompt.py`；WS-O3 §6 九文件集（conftest 口径） | 0 / 0 / 0 / 1 | 166 P；104 P / 1 S；2 P；4 F / 169 P | 逐项与各目标报告基线**逐条一致**，无新增失败 |
| 5 | `cd apps/desktop && npm run typecheck` | 未运行 | — | 本机无 `node_modules`、npm registry 不可达；按 SPEC §5 `E15` 记「未完整验证」，去向下一轮/宿主环境 |

`tests/ci` 的 4 条既有失败（缺少版本库内工作流文件）：

- `tests/ci/test_classify_changes.py::test_every_lane_reaches_the_composite_action`（缺 `.github/actions/detect-changes/action.yml`）
- `tests/ci/test_classify_changes.py::test_ci_jobs_only_gate_on_detect_outputs_that_detect_actually_declares`（缺 `.github/workflows/ci.yaml`）
- `tests/ci/test_live_comment.py::test_workflow_watch_list_names_a_workflow_that_exists`（缺 `.github/workflows/ci-review-comment.yml`）
- `tests/ci/test_live_comment.py::test_poller_never_watches_its_own_workflow`（缺 `.github/workflows/ci-review-comment.yml`）

九文件集的 4 条既有失败（均在 `tests/tools/test_tool_search.py::TestDeferredCallSchemaProbe`，`json.loads(None)`，改前改后一致）：

- `test_invalid_enum_is_blocked_before_dispatch`
- `test_validator_reports_nested_constraint_path[nested_type-arguments0-arguments.options.count-type]`
- `test_validator_reports_nested_constraint_path[nested_required-arguments1-arguments.options-required]`
- `test_validator_reports_nested_constraint_path[nested_extra-arguments2-arguments.options.additionalProperties]`

### 回滚方式

- 发布前（当前）：丢弃暂存克隆内对应文件改动即可，见 `runs/gugu-c2-o1/reports/VERIFY.md`「回滚方式」。
- 发布后：`git -C $W revert <提交>`，或按 `T-02`/`T-01`/`T-03` 各自报告 §回滚 逐文件回退；`GUGU_VERSION`/`docs/CHANGELOG-gugu.md`/README 版本表随发布提交一并 revert。
- 已建 `state.db` 的 `plan_mode_state` 为新增独立表，SessionDB 忽略，无数据迁移；如需彻底清理可 `DROP TABLE plan_mode_state`。

### 已知限制

1. **本地 `gugu-work` EROFS**：真实工作仓 `/home/a7067567g/下载/deepseek/60-外部仓库/gugu-work` 为只读挂载，本轮所有代码/版本改动落**暂存克隆** `runs/gugu-c2-o1/.scratch/gugu-work`。
2. **经暂存克隆发布到 fork**：发布 = 经本机代理 `http://127.0.0.1:7897` 推 `origin`（`https://github.com/7067567g/gugu.git`）的 `main` 快进 + `gugu-v0.1.0` tag；**绝不 force push、绝不 push upstream**。
3. **需宿主 fast-forward**：本地工作仓需宿主/人工执行恢复命令：
   `git -C /home/a7067567g/下载/deepseek/60-外部仓库/gugu-work fetch origin --tags && git -C /home/a7067567g/下载/deepseek/60-外部仓库/gugu-work merge --ff-only origin/main`
4. **desktop 未验证（E15）**：本机无 `node_modules`、npm registry 不可达，`npm run typecheck` 本轮**未运行**，桌面端未完整验证；按 SPEC §5 `E15` 标记，去向下一轮或宿主环境。
5. **三目标为最小子集**：`T-02` 仅接线 fail-closed/无人类分支（人类 deny/timeout/cancel 等仍用既有 outcome 字符串）；`T-01` 只交付只读 API（未接 CLI/RPC 输出）；`T-03` 的 pending 不做自动落盘 `.hermes/plans/`。同族扩展回 `reports/BACKLOG.md`。
