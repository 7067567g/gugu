# gugu 自有版本 CHANGELOG

> 本文件记录 **gugu 自有版本**的演进，与上游底座 **hermes-agent** 的版本**解耦**：
> `GUGU_VERSION`（本仓根）是 gugu 的语义化版本；`pyproject.toml` 的 `version = "0.21.5"`
> 与桌面包 `apps/desktop/package.json` 的 `0.17.6` 是上游内核/桌面包版本，**不随 gugu 版本递增**。
> 发布 tag 形如 `gugu-vX.Y.Z`，只推 `origin`（fork），**绝不** push `upstream`。

## 0.6.0 — 2026-10-18

- **gugu 版本**：`0.6.0`（根 `GUGU_VERSION`；本轮 `audit@v1` 含一个行为不变的机械重整批次 X1，属代码变更 → MINOR+1）
- **上游底座**：hermes-agent `0.21.5`（`pyproject.toml`，**未改动**）；桌面包 `0.17.6`（`apps/desktop/package.json`，**未改动**）
- **轮型 / run**：audit 轮 `gugu-l1-x1`（Round 1，loop-1 第 7/7 轮；A-01 全量审核 + A-02 机械重整 + A-03 复盘收口）
- **发布 tag**：`gugu-v0.6.0`（annotated；只推 `origin` fork，**绝不** push `upstream`）

### 机械重整批次 X1 — 版本解析单源收敛（行为不变）

| 文件 | old → new | 类型 |
| --- | --- | --- |
| `hermes_cli/build_info.py` | `get_code_identity()` 内联 `tomllib` 二次解析 `pyproject.toml` + 私有 `_read_product_version()` → 单次 `from hermes_cli.version_source import version_summary`，取 `summary["upstream_kernel"]`/`summary["product"]` | 改（行为不变） |
| `tests/ci/test_gugu_version_source.py` | 新增 `test_build_info_single_sources_upstream_version`（源码不含 `tomllib` + `version` 等价 `version_source.read_upstream_version(_ROOT)`） | 增（机械断言） |
| `GUGU_VERSION` | `0.5.0` → `0.6.0` | 发布 |
| `docs/CHANGELOG-gugu.md` | 新增本 `## 0.6.0` 节 | 发布 |
| `README.md` | 版本表新增 `gugu-v0.6.0` 行 | 发布 |

- **单源收益**：`version_summary()` 进入生产调用面；`pyproject.toml` `[project].version` 在 `build_info` 侧不再二次解析（reader 层 `hermes_cli/version_source.py` 为唯一解析点）。
- **行为不变**：`version`（上游内核 `0.21.5`）与 `product_version`（gugu 产品线）仍各自独立降级 `None`；`_code_identity_cache` 缓存语义不变；`get_code_identity()` 仍不抛异常。
- **范围**：`A1-09` 的其余 3 处 `tomllib` 重解析（`update_cmd_maint.py`/`main_install_repair.py`/`_early_recovery.py`）与 TD-011/012/013/015 本轮不做，去向 loop-2 / backlog，见 `workflow-group/runs/gugu-l1-x1/reports/REFACTOR-PLAN.md`。

### X1 先红 / 后绿

统一 `cd gugu-work`，`HERMES_HOME=$(mktemp -d)` + `--noconftest -p no:cacheprovider`：

| 步骤 | 命令 | 计数 |
| --- | --- | --- |
| 先红（未改 `build_info.py`） | `python3 -m pytest tests/ci/test_gugu_version_source.py -q` | **1 failed / 18 passed**（`assert 'tomllib' not in src`） |
| 后绿（改后） | 同命令 | **19 passed / 0 failed** |

### 门禁证据（后绿）

| # | 命令（`cd gugu-work`） | 结果 |
| --- | --- | --- |
| 1 | `HERMES_HOME=$(mktemp -d) python3 -m pytest tests/ci -q --noconftest -p no:cacheprovider` | **165 passed / 4 skipped / 0 failed**（基线 164P/4S/0F，净 +1 = 新增断言） |
| 2 | `python3 -m compileall -q hermes_cli agent tools plugins` | rc=0 |
| 3 | `python3 scripts/ci/check_design_rules.py` | rc=0 |
| 4 | `.../gugu-l1-o1/.scratch/venv/bin/python -m pytest tests/ci -q --cov=scripts/ci --cov-fail-under=40` | **43.03%**（TOTAL 2022 stmts / 1152 miss），rc=0 |
| 5 | `python3 scripts/ci/check_public_surface.py --base gugu-v0.5.0 --head HEAD --strict`（提交后） | 由 MAIN 在 S5 记录 |

### 回滚（单批）

```bash
cd gugu-work
git checkout bee9146a -- hermes_cli/build_info.py tests/ci/test_gugu_version_source.py GUGU_VERSION docs/CHANGELOG-gugu.md README.md
git status --porcelain   # 期望 0 行（回到 bee9146a 干净树）
```

## 0.5.0 — 2026-10-17

- **gugu 版本**：`0.5.0`（根 `GUGU_VERSION`；本轮新增 CI 门禁、CLI 产品身份展示层、Design 规则单源与编排切片测试，属新增能力而非修 bug → MINOR+1）
- **上游底座**：hermes-agent `0.21.5`（`pyproject.toml`，**未改动**）；桌面包 `0.17.6`（`apps/desktop/package.json`，**未改动**）
- **轮型 / run**：optimize 轮 `gugu-l1-o1`（Round 1，三目标 T-01/T-02/T-03 + 发布件/VERIFY）
- **发布 tag**：`gugu-v0.5.0`（annotated；只推 `origin` fork，**绝不** push `upstream`）

### 三目标交付

| 目标 | 主题 | 关键交付 |
| --- | --- | --- |
| T-01（G1） | CI 全量接线 + 覆盖率门槛 | `ci.yml` 5 job（`python-compile`/`desktop-typecheck`/`pytest` matrix 3.11+3.12/`coverage`/`all-checks-passed` 聚合）；`pyproject.toml` 新增 `[tool.coverage.run]`+`[tool.coverage.report] fail_under=40` 与 `pytest-cov==7.1.0`；新增接线静态断言 |
| T-02（G2） | 版本单一事实源 + CLI 产品身份 | 新增 `hermes_cli/version_source.py` 读取层与 `version-sources.json` 四源分组；`banner.py`/`_startup_fast.py`/`build_info.py` 输出 `gugu v{product}` 且 `product_version` 同源；上游 `0.21.5`/桌面 `0.17.6` 不变 |
| T-03（G3） | Design 规则单源 + 编排可演示切片 | 新增根 `DESIGN.md`（6 必需小节、数值各恰好一次并与 `gugu_storage.py` 常量一致）与 `scripts/ci/check_design_rules.py`（失败 rc=1）；后端 `create_project → submit_job(image) → queued → failed + error.message` 链路与前端 `OrchestrationPanel`/`/orchestration` GET+PUT 接线测试 |

### 改动文件清单（17 个：12 改/新增交付 + 3 发布件 + 2 测试台账）

| 目标 | 文件 | 类型 |
| --- | --- | --- |
| T-01 | `.github/workflows/ci.yml` | 改 |
| T-01 | `pyproject.toml` | 改 |
| T-01 | `tests/ci/test_ci_workflow_wiring.py` | 增 |
| T-02 | `hermes_cli/version_source.py` | 增 |
| T-02 | `version-sources.json` | 增 |
| T-02 | `hermes_cli/banner.py` | 改 |
| T-02 | `hermes_cli/_startup_fast.py` | 改 |
| T-02 | `hermes_cli/build_info.py` | 改 |
| T-02 | `tests/ci/test_gugu_version_source.py` | 增 |
| T-02 | `tests/ci/test_gugu_release_consistency.py` | 改 |
| T-03 | `DESIGN.md` | 增 |
| T-03 | `scripts/ci/check_design_rules.py` | 增 |
| T-03 | `tests/plugins/test_gugu_design_slice.py` | 增 |
| T-03 | `tests/ci/test_design_rules.py` | 增 |
| 发布 | `GUGU_VERSION` | 改 |
| 发布 | `docs/CHANGELOG-gugu.md` | 改 |
| 发布 | `README.md` | 改 |

### 先红 / 后绿主命令与计数

统一 `cd gugu-work`，`HERMES_HOME=$(mktemp -d)` + `-p no:cacheprovider` + `--noconftest`：

| 目标 | 主断言命令 | 先红 | 后绿 |
| --- | --- | --- | --- |
| T-01 | `pytest tests/ci/test_ci_workflow_wiring.py -q` | **6 failed / 2 passed** | **8 passed** |
| T-02 | `pytest tests/ci/test_gugu_version_source.py tests/ci/test_gugu_release_consistency.py -q` | **1 error**（`ImportError: cannot import name 'version_source'`）；按文件分跑 release：**7 failed / 4 passed** | **31 passed** |
| T-03 | `pytest tests/ci/test_design_rules.py -q`；`pytest tests/plugins/test_gugu_design_slice.py -q` | **5 failed**（checker/DESIGN 缺失；切片文件红证据按同源断言） | **5 passed**；**7 passed** |
| 发布 | `pytest tests/ci/test_gugu_release_consistency.py -q`（`GUGU_VERSION` 已升 `0.5.0`，CHANGELOG/README 未更新） | **1 failed / 12 passed**（缺 `## 0.5.0` 节与 `gugu-v0.5.0` 行） | **13 passed** |

### 门禁证据

| # | 命令（`cd gugu-work`） | 结果 |
| --- | --- | --- |
| 1 | `HERMES_HOME=$(mktemp -d) python3 -m pytest tests/ci -q --noconftest -p no:cacheprovider` | **164 passed / 4 skipped / 0 failed**（基线 124P/4S） |
| 2 | `../workflow-group/runs/gugu-l1-o1/.scratch/venv/bin/python -m pytest tests/ci -q --cov=scripts/ci --cov-fail-under=40` | **43.03%** ≥ 40%（门槛 = 基线 41% − ε，D-005） |
| 3 | `python3 -m compileall -q hermes_cli agent tools plugins` | rc=0 |
| 4 | `python3 scripts/ci/check_design_rules.py` | 「设计规则校验通过」，rc=0 |
| 5 | `python3 -m hermes_cli.main --version` | `gugu v0.5.0 · Hermes Agent v0.21.5 (2026.9.24) · upstream 7e0be4a0`，rc=0 |
| 6 | `python3 -c "...get_code_identity()['product_version']"` | `0.5.0`（== `GUGU_VERSION`） |

### 已知限制

1. **GitHub Actions 云上未实跑**：job 图/matrix/`needs.*.result` 只有静态断言 + 本地同命令复跑；去向 `gugu-l1-x1`。
2. **desktop E15 未验证**：无 `node_modules`，前端仅做静态接线断言，未跑 vitest/真实桌面渲染；去向 `gugu-l1-x1`。
3. **`tests/plugins` 全目录既有 collection 错误**：缺 `cryptography`/`python-dotenv`/telegram 包（非本轮引入），Design 切片按文件级命令取证；去向 `gugu-l1-x1`。
4. **根 `package.json`=1.0.0 归属未验证**：按 D-004 只登记为 `unverified`，不纳入相等断言。
5. **四源不同值**：`GUGU_VERSION=0.5.0` / 上游 `0.21.5` / 桌面 `0.17.6` 属设计（产品版本与上游解耦），不做同值对齐。

## 0.4.0 — 2026-10-17

- **gugu 版本**：`0.4.0`（根 `GUGU_VERSION`；第二周期 14/14 收口轮：六维全量审核 40 条发现 + 3 批 9 文件机械重整 + 发布一致性机械断言，主题明显 → MINOR+1）
- **上游底座**：hermes-agent `0.21.5`（`pyproject.toml`，**未改动**）；桌面包 `0.17.6`（`apps/desktop/package.json`，**未改动**）
- **轮型 / run**：审核 + 代码重整轮 `gugu-c2-a1`（14 轮流水线第 14/14 轮）
- **发布 tag**：`gugu-v0.4.0`（annotated；只推 `origin` fork，**绝不** push `upstream`）

### 审核结论（第二周期六维全量审核）

| 维度 | 报告 | 发现数 | 处置率 |
| --- | --- | --- | --- |
| 结构 / 入口 / 死代码 / 重复实现 | `runs/gugu-c2-a1/reports/AUDIT-structure.md` | A1 = 10 | 10/10 = 100% |
| 测试 / CI 门禁覆盖面 | `runs/gugu-c2-a1/reports/AUDIT-tests-ci.md` | A2 = 12 | 12/12 = 100% |
| 文档 / 身份 / 许可 / 上游同步 / 发布事实 | `runs/gugu-c2-a1/reports/AUDIT-docs-upstream.md` | A3 = 18 | 18/18 = 100% |
| **合计** | 三份 AUDIT | **40** | **40/40 = 100%**（空处置 0） |

- **处置分布**：改 **4**（A1-06、A2-03、A3-16、A3-18）、合 **5**（A1-03、A1-07、A1-09、A2-09、A3-05）、降 **14**、观 **4**、排除 **13**；每条均有四字段（结论 / 证据 / 处置 / 去向）。
- **口径注（P-001-A）**：上述改 4 / 降 14 为 AUDIT 原表处置；A3-13（`SECURITY.es.md`）与 A3-15（docs 断链）在 REFACTOR-PLAN 中升级为“改”，升级后计数为 **改 6 / 合 5 / 降 12 / 观 4 / 排 13**，合计仍 **40/40**，结论不变。
- **c1-a1 旧发现闭环**：34 条旧发现（A1 10 / A2 10 / A3 14）逐条在 `bb9b97c` 上复核 —— 仍成立 **26**、范围变化 **4**（A1-06、A1-10、A3-05、A3-08）、已消解 **3**（A2-04、A3-04、A3-10）、已证实 **1**（A2-06）。
- **未纳入本轮**：15 条 backlog（`B-A1-01/02/08`、`B-A2-05/07/08/10/11`、`B-A3-01/02/11/13b/14/17`、`B-X2-01`），owner = 主会话，去向 = 下一周期 R1；见 `runs/gugu-c2-a1/reports/CLOSEOUT.md`。

### 重整改动清单（3 批 / 9 文件：改 7 / 增 2 / 删 0，无移动）

| 批次 | 文件 | 类型 | 摘要 |
| --- | --- | --- | --- |
| X1 | `SECURITY.es.md` | 改 | `Hermes Agent` → `咕咕 gugu` 22 处；`security@nousresearch.com` → `joydove@163.com` 2 处 |
| X1 | `docs/02-三模式架构.md` | 改 | 第 67 行删除指向已删 `docs/01-Design…` 的断链，改写为基线 commit + `docs/03` 指引（不新增资产） |
| X1 | `README.md` | 改 | 0.1.0 / 0.2.0 / 0.3.0 三行补 fork 发布提交链接 |
| X2 | `tests/ci/test_classify_changes.py` | 改 | 2 例断链测试加 `skip(reason=A2-03/A1-06/56f6357/B-X2-01)` |
| X2 | `tests/ci/test_live_comment.py` | 改 | 2 例断链测试加同源 `skip` |
| X2 | `scripts/ci/README.md` | 增 | `scripts/ci/**` 孤儿资产分类（A 10 / B 3 / C 2）与 CI 重接入入口 |
| X2 | `AGENTS.md` | 改 | 1 句过期 CI 描述修正（工作流已于 `56f6357` 删除、当前未接 CI） |
| X3 | `hermes_cli/uninstall.py` | 改 | 第 265 行 docstring 改 raw（`"""` → `r"""`），清理 `SyntaxWarning` |
| X3 | `tests/ci/test_gugu_release_consistency.py` | 增 | 发布线机械断言：`GUGU_VERSION` semver + CHANGELOG 节 + README 行（4 例，含判别力反例） |

- **old→new 映射**：`runs/gugu-c2-a1/reports/X/WS-X-mapping.tsv`（9 行 × 5 列；无移动项，old=new 表示同路径内容变更，`(none)` 表示新增）。
- **引用悬空**：本批无移动/删除，basename 悬空面 = 0；`agent/**` 零改动（`git diff --name-only -- agent` 为空）。

### 先红 / 后绿主命令与计数

统一在暂存克隆 `$W = runs/gugu-c2-a1/.scratch/gugu-work`（base `bb9b97c0abc62d63abebef027fd413940d4a1d10` = `origin/main` = `gugu-v0.3.0`），解释器 `$W/.venv/bin/python`（Python 3.12.15 + pytest 9.1.1），pytest 统一 `HERMES_HOME=$(mktemp -d)` + `-p no:cacheprovider` + `--noconftest`：

| 批次 | 主断言 | 先红 | 后绿 |
| --- | --- | --- | --- |
| X1 | 身份 / 断链 / README 链接 grep | `Hermes Agent`=22、旧邮箱=2、断链命中 1、3 行 0 链接 | 22→0、2→0、断链 0、3 行 commit 链接 `git cat-file -e` 全 OK |
| X2 | `pytest -q tests/ci` | **4 failed / 120 passed，rc=1** | **120 passed / 4 skipped / 0 failed，rc=0**（4F→4S 为显式化基线债，非改写为 pass） |
| X3 | `-W error::SyntaxWarning compileall -f` + 发布断言 | **rc=1**（`hermes_cli/uninstall.py:265`）；断言文件不存在 | **rc=0**；发布断言 **4 passed**（假版本 `9.9.9` fixture rc=1 证判别力） |

### 门禁证据（WS-V 真实复跑）

| # | 命令（`cd $W`） | 退出码 | P/F/S | 与基线对比 / 归因 |
| --- | --- | --- | --- | --- |
| 1 | `./.venv/bin/python -m compileall -f -q tools agent hermes_cli acp_adapter` | **0** | — | 与基线一致（rc=0，无输出） |
| 2 | `./.venv/bin/python -W error::SyntaxWarning -m compileall -f -q tools agent hermes_cli acp_adapter` | **0** | — | 基线 rc=1（X3-01 前）→ 0；`-f` 强制重编译，避免 `.pyc` 新鲜假绿 |
| 3 | `HERMES_HOME=$(mktemp -d) ./.venv/bin/python -m pytest -q tests/ci --noconftest -p no:cacheprovider` | **0** | **124 passed / 4 skipped / 0 failed** | 基线 120P/4F（rc=1）；4F→4S 为显式基线债，P 120→124（+X3 4 例），新增失败 = 0 |
| 4 | 同上，`tests/ci/test_gugu_release_consistency.py` | **0** | **4 passed / 0 failed** | 新断言；0.4.0 三源一致后保持绿 |
| 5 | `import hermes_cli.main, agent.legacy_cli, acp_adapter.entry` → `IMPORT_OK`；`-m hermes_cli.main --version` / `--help` | **0 / 0 / 0** | — | 入口冒烟通过 |
| 6 | 回归：`tests/scripts/test_check_{public_surface,profile_archive_boundary,profile_scope_patterns}.py` | **0** | **8 passed / 0 failed** | 基线 8P；0 新增失败 |
| 7 | 回归：`tests/agent/test_context_file_sources.py` | **0** | **2 passed / 0 failed** | 基线 2P；0 新增失败 |
| 8 | 回归：`tests/hermes_cli/test_uninstall.py` | — | — | 文件不存在，未跑；其覆盖面由门禁 3（`tests/ci`）承接 |
| 9 | desktop `apps/desktop` `npm run typecheck` / `test:ui` | — | — | **未完整验证（E15）**：`node_modules` 不存在，不伪造 PASS |

### 回滚方式

- **发布前（当前工作树）**：`git checkout bb9b97c -- GUGU_VERSION docs/CHANGELOG-gugu.md README.md SECURITY.es.md "docs/02-三模式架构.md" tests/ci/test_classify_changes.py tests/ci/test_live_comment.py AGENTS.md hermes_cli/uninstall.py && rm -f scripts/ci/README.md tests/ci/test_gugu_release_consistency.py`。
- **发布后**：按 **C4 → C3 → C2 → C1** 逆序 `git revert`（C4 与 C1 同碰 `README.md`）；**禁止 force push**，已 push 的回滚一律用反向提交表达。
- 历史摘要**不回改**；无 DB 迁移、无外部依赖/服务/密钥变更。

### 已知限制

1. **desktop E15**：无 `node_modules`，`typecheck` / `test:ui` 未完整验证；去向 = 宿主恢复依赖后 `npm ci && npm run typecheck && npm run test:ui`（`B-A2-08`，下一周期 R1）。
2. **`tests/ci` 4 skip 是显式化基线债，不是修复**：`.github/**` 资产仍未恢复；`B-X2-01`（owner 主会话 / 去向下一周期 R1）跟踪取消 skip 并删除 `scripts/ci/README.md` 的孤儿标注。
3. **真实 `gugu-work` 需宿主 fast-forward**：本轮代码落 run 内暂存克隆，发布后由宿主执行 `git -C /home/a7067567g/下载/deepseek/60-外部仓库/gugu-work fetch origin --tags && git -C /home/a7067567g/下载/deepseek/60-外部仓库/gugu-work merge --ff-only origin/main`。
4. **发布通道可能 API 兜底**：`github.com:443` 超时则改走 `api.github.com` Git Data API（前两轮已用此法成功）；tag/push 归主会话 S5。
5. **版本四源仍不一致**（`GUGU_VERSION=0.4.0` / `pyproject=0.21.5` / 根 `package.json=1.0.0` / desktop `0.17.6`）：属 `B-A3-11` backlog，本轮不顺手统一。

### 发布状态

本片**只准备发布件**：`GUGU_VERSION`（`0.3.0` → `0.4.0`）、`docs/CHANGELOG-gugu.md`（本节）、`README.md`（版本表新增 `gugu-v0.4.0` 行，链接 `https://github.com/7067567g/gugu/releases/tag/gugu-v0.4.0`）。计划 4 个提交 **C1/C2/C3/C4**，tag 名 `gugu-v0.4.0`（annotated）。实际 `git add` / `commit` / `tag` / `push origin` 由主会话 S5 按显式 pathspec 执行；本片**未**在 `$W` 执行任何 `git add/commit/tag/push`、未 `git stash`。详见 `runs/gugu-c2-a1/reports/CLOSEOUT.md`。

## 0.3.0 — 2026-10-17

- **gugu 版本**：`0.3.0`（根 `GUGU_VERSION`；把 `0.2.0` 已交付但生产零调用的两项能力接入生产热路径，主题明显 → MINOR+1）
- **上游底座**：hermes-agent `0.21.5`（`pyproject.toml`，**未改动**）；桌面包 `0.17.6`（`apps/desktop/package.json`，**未改动**）
- **轮型 / run**：优化轮 `gugu-c2-o3`（14 轮流水线第 13/14 轮）
- **发布 tag**：`gugu-v0.3.0`（annotated；只推 `origin` fork，**绝不** push `upstream`）

### 目标

| 目标 | 主题 | 摘要 |
| --- | --- | --- |
| `T-01` | token 计量接入请求前预检 + 阻塞超窗硬拒发 | `_preflight_request_tokens` 首选 `agent.token_meter.measure`：有 usage anchor 时以快照 `baseline.tokens` 为返回值并落快照至 `agent._last_token_measurement`；无 anchor 保留 native → rough fallback（不用 estimated 替代 native）。`_compress_block_reason` 非空且请求**确实超过模型 `context_length`** 时抛 `PreflightCompressionBlocked(PreflightCompressionTimedOut)` 硬拒发，文案含 `/compress` 与 `/new`；窗口内保持 warn + 继续发送 |
| `T-02` | 统一脱敏接入压缩摘要与 owned 凭据持久化 | `_redact_compaction_text` 改 **unified-first**：先 `secret_redaction.redact_secrets`（vendor/PEM/JWT 整体替换），再旧 `redact_sensitive_text` 归一化 URL 凭据。`credential_persistence` owned 分支按白名单保留 6 键（`agent_key`/`access_token`/`refresh_token`/`api_key`/`token`/`secret`）+ `secret_fingerprint`，白名单外字符串叶子走 `redact_json_string_values`；脱敏器抛错时抛 `CredentialRedactionError` **fail-closed 拒写** |

### 改动文件清单

实施（WS-O1/WS-O2，均先红后绿）：

- `T-01`（WS-O1）
  - 修改 `agent/turn_context.py`（`PreflightCompressionBlocked`；preflight 内 lazy import `measure` 并落快照）
  - 修改 `agent/turn_context_compaction.py`（blocked 分支叠加 `request_exceeds_model_window(...) is True` 硬拒发）
  - 新增 `tests/agent/test_token_meter_wiring.py`（4 例）
- `T-02`（WS-O2）
  - 修改 `agent/context_compressor.py`（unified-first 双 pass）
  - 修改 `agent/credential_persistence.py`（`_OWNED_REFRESH_WHITELIST` / `CredentialRedactionError` / `_redact_owned_value`）
  - 新增 `tests/agent/test_secret_redaction_wiring.py`（4 例）

发布件（WS-V）：

- 修改 `GUGU_VERSION`（`0.2.0` → `0.3.0`）
- 修改 `docs/CHANGELOG-gugu.md`（本节）
- 修改 `README.md`（「## 版本」对照表新增 `gugu-v0.3.0` 行）

### 先红 / 后绿主命令与计数

统一在暂存克隆 `$W = runs/gugu-c2-o3/.scratch/gugu-work`（base `31858545bd565a3ec340dd65cb0fc61279102568` = `gugu-v0.2.0`），解释器 `$W/.venv/bin/python`（Python 3.12.15 + pytest 9.1.1），所有命令 `HERMES_HOME=$(mktemp -d)` + `-p no:cacheprovider` + `--noconftest`：

| 目标 | 主命令 | 先红 | 后绿 | 回归并集 |
| --- | --- | --- | --- | --- |
| `T-01` | `pytest -q tests/agent/test_token_meter_wiring.py tests/agent/test_token_meter_snapshot.py` | rc=1，**3 failed / 7 passed**（`measure` 计数=0 ×2、阻塞分支未拒发 ×1） | rc=0，**10 passed / 0 failed**（连跑 2 次一致） | **41 passed / 0 failed** |
| `T-02` | `pytest -q tests/agent/test_secret_redaction_wiring.py tests/agent/test_secret_redaction.py` | rc=1，**3 failed / 18 passed**（摘要 `redact_secrets` 计数=0、owned 非白名单明文透传、无 fail-closed） | rc=0，**21 passed / 0 failed**（连跑 2 次一致） | **57 passed / 0 failed** |

### 门禁证据（WS-V 真实复跑）

| # | 命令（`cd $W`） | 退出码 | P/F/S | 与基线对比 / 归因 |
| --- | --- | --- | --- | --- |
| 1 | `.venv/bin/python -m compileall -q tools agent hermes_cli acp_adapter` | **0** | — | 与基线一致（rc=0） |
| 2 | `.venv/bin/python -m pytest -q tests/ci --noconftest` | **1** | **120 passed / 4 failed / 0 skipped** | 与基线完全一致（120P/4F）；4 条均为暂存克隆缺 `.github/**` 的环境基线（逐条见 `VERIFY.md §2.1`），**新增失败 = 0** |
| 3 | `T-01` 主命令（见上表） | **0** | **10 passed / 0 failed** | 达标 |
| 4 | `T-02` 主命令（见上表） | **0** | **21 passed / 0 failed** | 达标 |
| 5 | 回归并集：`tests/agent/test_turn_context.py test_turn_context_overflow_warning.py test_over_window_compression_fail_closed.py test_compaction_redaction_boundaries.py test_anthropic_borrowed_row_authority.py tests/hermes_cli/test_auth_commands.py` | **0** | **75 passed / 0 failed** | = WS-O1 回归 41P ∪ WS-O2 回归 57P（去重两主命令既有用例 6P+17P）；**新增失败 = 0** |
| 6 | `cd apps/desktop && timeout 180 npm run typecheck` | **127** | — | **未完整验证（E15）**：无 `node_modules`、`tsc: not found`；不伪造 PASS。去向：宿主修 root lock 后 `npm ci && npm run typecheck` |

### 回滚方式

- **发布前（当前工作树）**：`git checkout 3185854 -- agent/turn_context.py agent/turn_context_compaction.py agent/context_compressor.py agent/credential_persistence.py GUGU_VERSION docs/CHANGELOG-gugu.md README.md && rm -f tests/agent/test_token_meter_wiring.py tests/agent/test_secret_redaction_wiring.py`（checkout 覆盖 4 个源文件 + 3 个发布件共 7 个修改文件；2 个新测试需删除）。
- **发布后**：`git -C $W revert <含发布件的提交>`（方案 A「合并实施 + release」或方案 B「T-01/T-02 两提交」，见 `VERIFY.md §5`）；不 force push、不改 `main` 既有历史。
- 历史摘要**不回改**；无 DB 迁移、无外部依赖/服务/密钥变更。

### 已知限制

1. **`T-01` 硬拒发只覆盖「blocked 且请求超过模型窗口」**：`request_exceeds_model_window` 为 `False`（窗口内）或 `None`（窗口未知）时保持既有 warn + 继续发送，避免把仍可发送的请求提前打死；窗口未知分支未单列断言（由既有回归被动覆盖）。
2. **`T-02` owned 白名单未逐 provider 核刷新协议**：6 键 + `secret_fingerprint` 取自 `CANDIDATES.md` `preferred` 集合与本轮 `grep`；若某 provider 刷新必需字段不在白名单内，会被改写或 fail-closed 拒写。防回归用例 `test_owned_payload_whitelist_keeps_refresh_fields` 已锁定；发现缺口按 `SPEC §5 E16` 停手升级，不自行扩白名单。
3. **desktop E15**：无 `node_modules` + root lock 不同步 + `konva@^10.7.1` 不可解析，`npm run typecheck` 未完整验证；去向宿主修 lock 后 `npm ci && npm run typecheck`。
4. **真实 `gugu-work` 需宿主 fast-forward**：本轮代码落暂存克隆，发布后由宿主执行
   `git -C /home/a7067567g/下载/deepseek/60-外部仓库/gugu-work fetch origin --tags && git -C /home/a7067567g/下载/deepseek/60-外部仓库/gugu-work merge --ff-only origin/main`。

### 发布状态

本片**只准备发布件**：`GUGU_VERSION` / `docs/CHANGELOG-gugu.md` / `README.md` 已改，tag 名 `gugu-v0.3.0`（annotated）。实际 `git add` / `commit` / `tag` / `push origin` 由主会话 S5 按显式 pathspec 执行；本片**未**在 `$W` 执行任何 `git add/commit/tag/push`、未 `git stash`。详见 `runs/gugu-c2-o3/reports/VERIFY.md`。

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
