# gugu 自有版本 CHANGELOG

> 本文件记录 **gugu 自有版本**的演进，与上游底座 **hermes-agent** 的版本**解耦**：
> `GUGU_VERSION`（本仓根）是 gugu 的语义化版本；`pyproject.toml` 的 `version = "0.21.5"`
> 与桌面包 `apps/desktop/package.json` 的 `0.17.6` 是上游内核/桌面包版本，**不随 gugu 版本递增**。
> 发布 tag 形如 `gugu-vX.Y.Z`，只推 `origin`（fork），**绝不** push `upstream`。

## 0.2.0 — 2026-10-17

- **gugu 版本**：`0.2.0`（根 `GUGU_VERSION`；三个新能力，主题明显 → MINOR+1）
- **上游底座**：hermes-agent `0.21.5`（`pyproject.toml`，**未改动**）；桌面包 `0.17.6`（`apps/desktop/package.json`，**未改动**）
- **轮型 / run**：优化轮 `gugu-c2-o2`（14 轮流水线第 11/14 轮）
- **发布 tag**：`gugu-v0.2.0`（annotated；只推 `origin` fork，**绝不** push `upstream`）

### 目标

| 目标 | 主题 | 摘要 |
| --- | --- | --- |
| `T-01` | MCP 传输关闭活性看门狗 + 404 重新初始化 | `TransportLivenessWatchdog`：每个 `Ready` 客户端挂一次性轮询器，传输关闭时发一条带 `client_id` 的 `TransportClosed` 并清 slot（可立即重臂）；状态漂移出 `Ready` 静默退出；同名替换连接不被旧事件误逐。`is_session_not_found`（404 + session 失效短语）+ `invalidate_schema_cache` + `recover_session_not_found`（先失效缓存再重新 initialize）；`_run_http` 异常分支接一处只读 seam，不阻断/不改既有重连语义 |
| `T-02` | 上下文预算硬绑定 + 可重放 token 计量快照 | 新增只读门面 `agent/token_meter.py`：`TokenMeasurement`（`log_revision` / `baseline` / 带符号 `surface_delta_tokens` / `total_tokens` / 逐节点拆分）；`measure()` 复用既有估算器，同 durable tail + 同 `log_revision` 重放一致，`usage` anchor 优先于 `estimated`；`assert_within_budget()` 把声明预算变成可断言硬绑定（超限 `BudgetExceededError`），不触发压缩/新 turn |
| `T-03` | 统一密钥脱敏纯函数 | 新增 `agent/secret_redaction.py`：`redact_secrets` 覆盖 vendor/AWS/PAT/JWT/Bearer/PEM/赋值式/URL 敏感查询参数 → `[REDACTED_SECRET]`；`MATCH_ANY` 快速短路（无命中返回原对象）；`redact_json_string_values` 递归处理 JSON 字符串值；`cron/incidents.py::_redact_error` best-effort 接线（叠加在既有 redactor 之后，不抛） |

### 改动文件清单

实施（WS-O1/O2/O3，均先红后绿）：

- `T-01`（WS-O1）
  - 修改 `tools/mcp_liveness.py`（`READY_STATE` / `TransportClosed` / `event_matches_client` / `transport_is_closed` / `TransportLivenessWatchdog` / `is_session_not_found` / `invalidate_schema_cache` / `recover_session_not_found`）
  - 修改 `tools/mcp_tool_transport.py`（`_recover_session_not_found` seam；`_run_http` 异常分支接一处）
  - 新增 `tests/tools/test_mcp_transport_liveness.py`（15 条 contract 测试；`tests/tools/test_mcp_liveness.py` 既有 7 条未改、只复用）
- `T-02`（WS-O2）
  - 新增 `agent/token_meter.py`
  - 新增 `tests/agent/test_token_meter_snapshot.py`（6 条 contract 测试）
- `T-03`（WS-O3）
  - 新增 `agent/secret_redaction.py`
  - 修改 `cron/incidents.py`（`_redact_error` +6 行，独立 `try/except`）
  - 新增 `tests/agent/test_secret_redaction.py`（17 条用例）
  - `agent/credential_persistence.py` **未改**（可选接线未做，见「已知限制」）

发布件（WS-V）：

- 修改 `GUGU_VERSION`（`0.1.0` → `0.2.0`）
- 修改 `docs/CHANGELOG-gugu.md`（本节）
- 修改 `README.md`（「## 版本」对照表新增 `gugu-v0.2.0` 行）

### 门禁证据（本片真实复跑）

统一在暂存克隆 `$W = runs/gugu-c2-o2/.scratch/gugu-work`（base `7879f3ab1ab3a99c660979c7d5fda84de415fe8a`），解释器 `$W/.venv/bin/python`（Python 3.12.15 + pytest 9.1.1），所有命令 `HERMES_HOME=$(mktemp -d)` + `-p no:cacheprovider`，`tests/ci` 与主命令加 `--noconftest`：

| # | 命令 | 退出码 | P/F/S | 与基线对比 |
| --- | --- | --- | --- | --- |
| 1 | `.venv/bin/python -m compileall -q tools agent hermes_cli acp_adapter` | 0 | — | 与基线一致（0） |
| 2 | `.venv/bin/python -m pytest -q tests/ci --noconftest` | 1 | 120 passed / 4 failed | 与基线一致（120P/4F；4 条均为暂存克隆缺 `.github/**` 的环境基线） |
| 3 | `.venv/bin/python -m pytest -q tests/tools/test_mcp_liveness.py tests/tools/test_mcp_transport_liveness.py --noconftest` | 0 | 22 passed / 0 failed | `T-01` 主命令 0 failed（7 既有 + 15 新增） |
| 4 | `.venv/bin/python -m pytest -q tests/agent/test_token_meter_snapshot.py tests/agent/test_context_compressor.py tests/tools/test_budget_config.py --noconftest` | 0 | 167 passed / 0 failed | `T-02` 主命令 0 failed（161 既有 + 6 新增） |
| 5 | `.venv/bin/python -m pytest -q tests/agent/test_secret_redaction.py --noconftest` | 0 | 17 passed / 0 failed | `T-03` 主命令 0 failed |
| 6 | 回归 mcp：`tests/tools/test_mcp_liveness.py tests/tools/test_mcp_stdio_dead_reconnect.py tests/tools/test_mcp_transport_group_reconnect.py tests/tools/test_mcp_schema_cache.py --noconftest` | 1 | 1 failed / 22 passed | 与基线逐条一致（唯一失败 = `test_mcp_schema_cache::TestWriteThroughPreservesSchema::test_cache_round_trip_reaches_agent_schema`，`ModuleNotFoundError: No module named 'mcp'`）；**新增失败 = 0** |
| 7 | 回归 T2：`tests/agent/test_compression_config_defaults.py tests/agent/test_replay_budget_accounting.py tests/agent/test_context_token_tracking.py --noconftest` | 0 | 19 passed / 0 failed | 与基线 T2 组一致（180P 中本子集 19P）；**新增失败 = 0** |
| 8 | 回归 T3：`tests/tools/test_spill_safety.py tests/cron --noconftest` | 1 | 45 failed / 1310 passed / 19 skipped / 6 errors | 与基线 T3 组逐条一致（51 条 `FAILED`/`ERROR` testid 集合 `diff` 相同）；**新增失败 = 0** |
| 9 | `cd apps/desktop && npm run typecheck` | 未运行 | — | **未完整验证（E15）**：无 `node_modules`；`npm ci --ignore-scripts` → `EUSAGE`（root `package.json`/`package-lock.json` 不同步，缺 `eslint@10.13.0` 等）；`npm install --legacy-peer-deps --ignore-scripts --no-package-lock` → `ETARGET notarget konva@^10.7.1`。去向：宿主环境修复 lock 后 `npm ci && npm run typecheck`，或下一轮重试 |

`tests/ci` 的 4 条既有失败（缺版本库内工作流文件）：

- `tests/ci/test_classify_changes.py::test_every_lane_reaches_the_composite_action`（缺 `.github/actions/detect-changes/action.yml`）
- `tests/ci/test_classify_changes.py::test_ci_jobs_only_gate_on_detect_outputs_that_detect_actually_declares`（缺 `.github/workflows/ci.yaml`）
- `tests/ci/test_live_comment.py::test_workflow_watch_list_names_a_workflow_that_exists`（缺 `.github/workflows/ci-review-comment.yml`）
- `tests/ci/test_live_comment.py::test_poller_never_watches_its_own_workflow`（缺 `.github/workflows/ci-review-comment.yml`）

### 回滚方式

- 发布前（当前工作树）：按各目标报告「回滚方式」逐文件恢复，例如
  `git checkout 7879f3a -- tools/mcp_liveness.py tools/mcp_tool_transport.py cron/incidents.py`，
  并删除新增文件 `agent/token_meter.py`、`agent/secret_redaction.py`、`tests/agent/test_token_meter_snapshot.py`、`tests/agent/test_secret_redaction.py`、`tests/tools/test_mcp_transport_liveness.py`；版本三文件随发布提交一并恢复。
- 发布后：`git -C $W revert <本轮发布提交>`（单提交含三目标 + 版本四件套；无 DB 迁移、无外部依赖变更）。

### 已知限制

1. **`T-01` (A) 未接生产生命周期**：gugu 各 transport 不暴露等价 `is_transport_closed` 句柄（全仓仅命中本模块文档字符串）、无 `client_id` 概念。可挂载点 `tools/mcp_tool_transport.py::_serve_session` 虽在 `PROJECT §0` 白名单内，但本机 `mcp` SDK 未安装（`import mcp` → `ModuleNotFoundError`），无法对真实 stdio/HTTP 传输做 E2E；按仓库根 `AGENTS.md` 的「Don't wire in dead code without E2E validation」，本目标只交付自包含机制 + 机械 contract 测试，不接生产。故 `transport_is_closed` 只能 duck-typing，真实 SDK transport 返回 `None`（保留 slot、不发假关闭），本判据在真实传输上「推断未验证」；按 `SPEC §5 E16` 豁免、不替上游修。
2. **`T-02` 未接真实调用点**：`measure` / `assert_within_budget` 目前由调用方显式使用，未在 `context_compressor` / `budget_config` 内埋检查点（只读门面 + 契约测试）；接入属后续优化项，避免改动压缩热路径。
3. **`T-03` 可选接线未做**：`agent/credential_persistence.py` 的 owned source 原样透传语义与统一值脱敏存在潜在冲突，未纳入最小子集；若后续接线需先断言 owned payload 行为不变。
4. **desktop E15**：无 `node_modules` + lock 不同步 + `konva@^10.7.1` 不可解析，`npm run typecheck` 未运行，桌面端未完整验证。
5. **`mcp` SDK 未安装**：无法对真实 stdio/HTTP 传输驱动关闭探测与 404；`T-01` 的 (B) 接线正确性靠 seam 测试 + code review，未做 E2E。基线 `test_mcp_schema_cache` 失败同因。

### 发布状态

本片**只准备发布件**：`GUGU_VERSION` / `docs/CHANGELOG-gugu.md` / `README.md` 已改，tag 名 `gugu-v0.2.0`（annotated）。实际 `git add` / `commit` / `tag` / `push origin` 由主会话 S5 按显式 pathspec 执行；本片**未**在 `$W` 执行任何 `git add/commit/tag/push`、未 `git stash`。详见 `runs/gugu-c2-o2/reports/VERIFY.md`「发布准备」。

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
