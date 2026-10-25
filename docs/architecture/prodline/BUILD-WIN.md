# Windows build lane（A03）— job 语义与静态门

```yaml
schema: gugu.prodline-build-win-doc/v1
status: authored-not-run
owner: area-a
updated: "2026-10-25"
depends_on: gugu-pl-a02-linux-build
```

> 机器可读 receipt：[`prodline/schemas/build-receipt.schema.json`](../../../prodline/schemas/build-receipt.schema.json)（`build-receipt/v1`，已冻结）。
> 可执行 gate：[`prodline/gates/build_gate.py`](../../../prodline/gates/build_gate.py)。
> workflow：[`../../.github/workflows/ci.yml`](../../../.github/workflows/ci.yml) 的 `build-win` job。
> 测试：[`prodline/tests/test_build_gate.py`](../../../prodline/tests/test_build_gate.py)、
> [`tests/ci/test_ci_workflow_wiring.py`](../../../tests/ci/test_ci_workflow_wiring.py)。

本文只描述 **A03 已落地的 job 结构 + 静态门**。它**不**声称 Windows 构建已经跑过：
本机没有 `pwsh` / `actionlint` / 干净 Win11 环境，`wine` 明确不算干净环境（plan-A F13）。
真实 runner 首跑与双构建档位裁决是 **A04** 的事。

## 1. 为什么需要这个 job

两个状态在 CI 里必须长得不一样：

| 状态 | CI 里的证据 | `build_gate check` |
| --- | --- | --- |
| **never ran**（lane 被删/被跳过/证据丢失） | 没有 receipt | `decision=missing`，**exit 3** |
| **ran and failed**（构建挂了 / 事件与档位不符） | 有 receipt，`status=fail` 或被拒 | `decision=fail`，**exit 1** |
| ran and passed | 有 receipt，`status=pass` 且档位与事件一致 | `decision=pass`，**exit 0** |

只有一个 receipt 的**存在性 + 内容**能区分前两者；因此 job 不允许 job 级 `if:`，
`record`/`check`/`upload` 都用 `if: always()`，构建失败也留下 `status=fail` 的 receipt。

## 2. job 结构（`ci.yml`）

```text
build-win  (windows-latest, timeout 90m, 无 job 级 if, 无 continue-on-error)
  checkout
  setup-python 3.12
  setup-node 24
  npm ci --legacy-peer-deps --no-audit --no-fund
  Build 1 of 2            npm run dist:win:nsis          # 所有事件
  Stage build 1 output    Move-Item release -> artifacts/build/win-1
  Build 2 of 2            npm run dist:win:nsis          # 仅 main ref（push/dispatch）
  Stage build 2 output    Move-Item release -> artifacts/build/win-2  # 同上
  Record receipt          python -m prodline.gates.build_gate record   # if: always()
  Gate event/tier         python -m prodline.gates.build_gate check    # if: always()
  Upload receipt          actions/upload-artifact@v4     # if: always()
```

- **单/双构建只由 step 级 `if` 控制**：`github.ref == 'refs/heads/main'`。
  PR（`refs/pull/N/merge`）只付一次 NSIS 构建；`push:main` 必须证明双构建；
  `workflow_dispatch` 在 main 上同样走双构建、在其它 ref 上走单构建——语义由 ref 决定，
  不由 `event_name` 决定，避免 dispatch 被误判成 PR 却带着 main ref。
- **job 级没有 `if:`**：GitHub 的 job-level skip 会让 `needs.build-win.result == "skipped"`；
  aggregator 的 `test ... = "success"` 会把它变成红，但“合法地不跑”仍然存在。A03 直接
  禁止 job 级 skip，让 lane 恒为 success/failure 之一。
- **`build-win` 进 `all-checks-passed.needs`**，且 aggregator 显式 echo/test 它。
  `tests/ci/test_ci_workflow_wiring.py` 的 `set(jobs) - {all-checks-passed} <= needs`
  断言保证以后新增 job 不能绕过 aggregator。
- 产物：`artifacts/build/build-receipt-win.json`（retention 14 天；`if-no-files-found: warn`，
  因为“没有 receipt”已经由 gate 的 exit 3 表达，不让 upload 把它变成另一个错误）。

## 3. receipt 口径（复用 `build-receipt/v1`，不新增 schema）

`record` 从**这次实际跑了几个 build**写 job 级证据。字段（全部在冻结 schema 内或
`additionalProperties: true`）：

| 字段 | 内容 |
| --- | --- |
| `schema` / `stage` | `build-receipt/v1` / `build` |
| `commit` / `commit_short` | `$GITHUB_SHA`（PR 上是 merge commit；A04 记录 run URL 时一并说明） |
| `provenance` | 恒 `real`（`fixture`/`mock`/`accelerated` 一律不得 PASS） |
| `status` | `pass` / `fail`（`skip` 永远不是 PASS） |
| `os` / `runner` | `windows` / `{os, image: windows-latest, arch: x64, python, git, node, npm}` |
| `artifacts[]` | 每个 build 的 `*.exe` / `*.msi`（`path`、`sha256`、`kind=windows-installer`） |
| `event` | `{name: pr\|push-main, ref, sha, run_id, run_attempt}` |
| `reproducibility` | `{mode, verdict, builds[], compared_fields[], exclusions[]}` |
| `producer` | `{stage: build, tool: "prodline.gates.build_gate record", contract_version: 1}` |

每个 `builds[i]` 记录 `index`、`root`、`files_count`、`files_sha256`（canonical：
`sha256␠␠relpath\n` 排序后整体哈希）、`bytes`，以及被排除的 `excluded_files`。

### 3.1 事件 ↔ 档位

| 事件 | 允许 mode | verdict | builds |
| --- | --- | --- | --- |
| `pr` | `single-build` | `not-attempted` | 1 |
| `push-main` | `byte-identical` / `manifest-normalized` | `equal` | 2 |

负样例（测试强制）：

- PR 的 receipt 声称 `byte-identical` → **红**（第二次 build 的 step 根本没跑）；
- `push-main` 的 receipt 声称 `single-build` → **红**（main 必须证明双构建）；
- `manifest-normalized` 没有非空 `exclusions` → **红**；
- `byte-identical` 却带了 `exclusions` → **红**；
- `status=skip`、`provenance != real`、`clean_build != true`、commit 不符 →
  全部 **红**；`verdict=differ` 永远不可能 `status=pass`。

### 3.2 档位与 `builtAt`

`apps/desktop/scripts/write-build-stamp.mjs` 把 `builtAt: new Date().toISOString()` 写进
`apps/desktop/build/install-stamp.json`，electron-builder 的 `extraResources` 又把它打进
包体（plan-A F6）。因此：

- `byte-identical`：两次 build 的**全部文件**逐字节相同（最强）；
- `manifest-normalized`：按显式 `--exclude`（相对路径 / fnmatch）排除后全部相同，
  且至少一个被排除文件确实不同；`exclusions` 必须写进 receipt；
- 否则 `verdict=differ`、`status=fail`。

A03 的 `ci.yml` **没有**传 `--exclude`：NSIS exe 本身嵌入了 `builtAt`，只排除
`install-stamp.json` 不能让它变等；把 exe 也排除则等于不比较交付物。所以 push 上的
双构建要么真的 byte-identical，要么诚实变红。`manifest-normalized` 的实现与测试在
`build_gate.py` 里保留，A04 只有在用 7z 解包逐文件 sha 证明“唯一差异是 builtAt”之后
才应启用它。A03 **不**声明 `builtAt` 是唯一差异——那是 A04 的裁决。

## 4. `build_gate` CLI 与退出码

```bash
# 记录（1 个目录 = pr；2 个目录 = push-main）
python -m prodline.gates.build_gate record \
  --event pr --commit "$GITHUB_SHA" \
  --build-dir artifacts/build/win-1 \
  --out artifacts/build/build-receipt-win.json

# gate（never ran / ran and failed 在这里分开）
python -m prodline.gates.build_gate check \
  artifacts/build/build-receipt-win.json \
  --event pr --expect-commit "$GITHUB_SHA"
```

| exit | `decision` | 含义 |
| --- | --- | --- |
| `0` | `pass` | receipt 存在、`status=pass`、事件/档位/commit/schema 全部一致 |
| `1` | `fail` | receipt 存在但被拒（`status=fail`、`skip`、档位与事件矛盾、非 real、commit 不符…） |
| `2` | `error` | 输入/配置错误（未知事件、JSON 不可读、schema 违反、`record` 参数错） |
| `3` | `missing` | **没有 receipt** → never ran / 证据丢失；`never_ran=true`、`ran_and_failed=false` |

`check --format json --json-out <path>` 输出 `gugu.prodline-build-gate/v1`，消费方
（A05 的 aggregator / A04 的报告）应把 `decision=missing` 与 `decision=fail` 分开记账，
不得把任一写成 PASS。

## 5. 静态门证明什么 / 不证明什么

**证明（本机真跑）**

- `ci.yml` 能被 `yaml.safe_load` 解析；无重复 key；`build-win` 的 `runs-on` 是
  `windows-latest`；job 级无 `if`、无 `continue-on-error`。
- 恰好两个 `dist:win:nsis` step：一个无 `if`，一个带 push-main 的 step 级 `if`。
- `build_gate record` / `check` / `--expect-commit` / `if: always()` / upload 都在。
- `build-win` 在 `all-checks-passed.needs`，且 aggregator 的 `test` 行点名它。
- `build_gate` 的正/负样例（24 个测试）全部真跑：PR+`byte-identical` 红、
  push+`single-build` 红、missing=exit 3、`status=fail`=exit 1、`provenance!=real` 红。

**不证明（明确 NOT-VERIFIED，不得写成 PASS）**

- `build-win` 没有在真实 `windows-latest` 上跑过（A04）；没有 NSIS 产物、没有 run URL。
- `npm ci` 在 `windows-latest` 是否需要私有 registry / 代理**未验证**；失败则 A04 写
  `BLOCKED`，不改写成“通过”。
- 本机没有 `actionlint`，workflow 只做了 YAML 解析 + 结构化断言，**没有**做 GitHub
  Actions schema lint。
- 双构建档位没有在真实产物上裁决（A04：byte-identical / manifest-normalized /
  not-verified 三选一 + 7z 差异清单）。
- `wine` 下跑出的任何东西都不作为证据。

## 6. 本地复核命令

```bash
cd /home/a7067567g/下载/deepseek/60-外部仓库/gugu-work
export PYTHONDONTWRITEBYTECODE=1

.venv/bin/python -m pytest prodline/tests/test_build_gate.py -q
.venv/bin/python -m pytest tests/ci/test_ci_workflow_wiring.py -q

# 结构冒烟：job 图 + 无 job 级 skip + needs 全覆盖
.venv/bin/python - <<'PY'
import yaml
from pathlib import Path
wf = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
jobs = wf["jobs"]
bw = jobs["build-win"]
assert bw["runs-on"] == "windows-latest"
assert "if" not in bw and "continue-on-error" not in bw
assert set(jobs) - {"all-checks-passed"} <= set(jobs["all-checks-passed"]["needs"])
print("build-win job graph ok:", len(jobs), "jobs")
PY
```

## 7. 边界与去向

- **不改**：`apps/desktop/scripts/write-build-stamp.mjs`（`SOURCE_DATE_EPOCH` 路线属
  A04，需 owner 批准）、`prodline/stages/build.py`（A02 的 Linux 产物）、6 个 receipt
  schema、`pipeline.yaml`、`scripts/install.ps1`、产品代码/打包结构。
- **A04**：真实 runner 首跑、run URL/job id、双构建档位 + 7z 差异清单、
  `install-stamp.json builtAt` 是否唯一差异（yes/no + 证据）、Windows 上跑
  `artifact_check` 消费 build-linux 的 artifact.zip。
- **A05**：`REQUIRED_JOBS` 补 build-linux / arch-gate，aggregator 负样例
  （`build-win=skipped` 必红），CI 基线归因。本轮只把 `build-win` 接进 needs。
