---
schema: gugu.prodline-ownership/v1
status: active
owner: release-manager
updated: "2026-10-25"
contract_version: 1
---
# prodline 归属与 P1 契约（v1）

> 本文回答：**谁拥有产线层、每个 stage 产出什么、6 个 receipt 的形状由谁定义、
> 哪些字段已经冻结、产品代码为什么不能 import 它。**
>
> 单一事实源是 [`prodline/pipeline.yaml`](../../../prodline/pipeline.yaml) 与
> [`prodline/schemas/`](../../../prodline/schemas/)。本文解释它们，不复制会漂移的字段清单；
> 字段以 schema 文件为准。长线计划见 [`LONG-LINE-PLAN.md`](LONG-LINE-PLAN.md)（该文档写于
> P1 之前，其中“3 schema”的口径由本文与 P1 .4 收口时统一）。

---

## 0. 一句话

`prodline/` 是 **P 类（产线工具）**：只在构建、安装、验收、取证、soak、测试时运行，
**产品进程（K/X/G）永不 import 它**。P1 只交付契约与 source 骨架；A/B 两区消费契约，
不各自重复定义。

---

## 1. 归属

| 对象 | owner | 说明 |
| --- | --- | --- |
| `prodline/**` | `release-manager` | arch 账本登记为 P；计数的任何变化需 release-manager 复核 |
| `prodline/pipeline.yaml` | `release-manager` | 5 个 stage id、stage→receipt 映射、accept 策略 |
| `prodline/schemas/*.json` | `release-manager` + 两区 review | v1 冻结后只加 optional 字段；破坏性变更必须新 v2 |
| stage `source` / 版本断言 | P1.2（`gugu-pl-p1-source`） | 只读 source 证据，无独立 receipt schema |
| stage `build` / artifact | A 区 | 产出 `artifact.zip` + `artifact-manifest.json` + `build-receipt/v1` |
| stage `install` / `runtime` | B 区 | 产出 `install/runtime/task/recovery` receipt |
| stage `quality` / 发布门 | B 区 + release-manager | 消费 `release-receipt/v1`，汇总 `acceptance-report.json` |
| `accept.py` / `receipt_check.py` | P1.3（`gugu-pl-p1-gate`） | 本轮只声明策略，不实现 |

**变更纪律**：改 schema 的 required / enum / type 视为语义变更，必须先在本文件与
`pipeline.yaml` 写明，再新开 v2；只加 optional 字段可在 v1 内做，但必须同时补正/负样例。

---

## 2. 五个稳定 stage

stage id 是跨区硬契约，**不得改名、不得增删**：

| # | stage id | order | 做什么 | 产出 receipt |
| --- | --- | --- | --- | --- |
| 1 | `source` | 10 | 只读 source / 版本线 / lockfile 断言；证据作为 build 的输入 | 无（固定 6 schema 不含 source） |
| 2 | `build` | 20 | 产出 `artifact.zip` + `artifact-manifest.json` | `build-receipt/v1` |
| 3 | `install` | 30 | 从 artifact 安装、复算 sha256 / 布局 / 版本 / `FILES.sha256` | `install-receipt/v1` |
| 4 | `runtime` | 40 | 首启 readiness、20 题样本、故障注入 | `runtime-receipt/v1`、`task-receipt/v1`、`recovery-receipt/v1` |
| 5 | `quality` | 50 | 升级 / 回滚 / 数据完整 + 最终验收聚合 | `release-receipt/v1` |

为什么 `source` 没有 receipt：P1 固定契约只列 6 个 receipt（`build/install/runtime/task/
recovery/release`）。source 的证据是 build 的输入，不单独发 receipt；若要新增，属于
**第 7 个 schema**，必须走 v2 + 主会话裁决，不得本轮偷偷加。

为什么 `task`/`recovery` 归 `runtime`：它们描述的是同一个运行阶段内的观测，不是新的
stage；`release` 归 `quality` 是因为升级/回滚是否可发布属于质量门，而不是安装动作本身。

### 2.1 source 证据（P1.2 已落地）

`prodline/stages/source.py` 只读收集一份 **source evidence**（`gugu.prodline-source-evidence/v1`），
不是第 7 个 receipt：

- 断言 4 条发布版本线：`product`（`GUGU_VERSION`，动态 semver）、`upstream_kernel`
  （`pyproject.toml#project.version`）、`upstream`（`arch/upstream.lock#tag`，并校验
  `commit == expected_commit`）、`upstream_desktop`（`apps/desktop/package.json#version`）；
- **B14**：登记 `bootstrap_installer_tauri`（`apps/bootstrap-installer/src-tauri/tauri.conf.json`），
  按字面量断言 `0.21.1`，防止这条独立版本线再漏登/漂移；
- 计算 `uv.lock` / `package-lock.json` 的 canonical（UTF-8、LF、无 BOM）sha256 与 raw sha256；
- 提供 `collect_source_evidence` / `hash_lockfile` / `verify_source_evidence` 三个 import 接口，
  供 A02 逐字抄写 manifest 的 `lockfiles`，不再另造读取逻辑。

字段、CLI、A02 消费方式与负样例见 [`SOURCE-EVIDENCE.md`](SOURCE-EVIDENCE.md)。

---

## 3. 六个 `v1` receipt

所有 receipt 都必填以下 6 个公共字段（schema 文件里逐个 `required`）：

| 字段 | 类型 / 约束 | 语义 |
| --- | --- | --- |
| `schema` | const `<name>-receipt/v1` | receipt 类型与版本 |
| `commit` | `^[0-9a-f]{40}$` | 证据绑定的 git commit；不接受短 sha |
| `generated_at` | date-time（必须带时区） | 产出时间 |
| `stage` | const，见 §2 映射 | 生产该 receipt 的 stage |
| `provenance` | `real｜fixture｜mock｜accelerated` | **R-P1-a**：证据档位，必填 |
| `status` | `pass｜fail｜skip` | 该 receipt 自身的结论；`skip` 不是 pass |

### 3.1 `provenance` 语义

- `real`：在真实产物 / 真实机器 / 真实凭据上观测得到；
- `fixture`：用自造 artifact 或假数据跑通链路，**不冒充 A 的产物**；
- `mock`：替身服务 / 替身模型；
- `accelerated`：缩短时钟（如 soak 压缩），不是真实时长。

**策略（`pipeline.yaml#acceptance.rules`）**：`provenance != "real"` 的 receipt 不得让该段
变成 PASS；`fixture/mock/accelerated` 只能证明链路，不能证明验收。该规则由 P1.3 的
`accept.py` 执行；本轮用测试锁住“策略已声明 + 这些档位形状合法但不得算 PASS”。

### 3.2 `install-receipt.artifact{}`（R-P1-b）

`install-receipt` 额外必填 `artifact`，且内部 5 个字段必填：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `path` | non-empty string | artifact.zip 的路径（人类可读定位） |
| `sha256` | 64 hex，**无 `sha256:` 前缀** | P 在安装机复算的整包 sha256 |
| `manifest_sha256` | 64 hex | 并列 manifest 文件的 sha256 |
| `provenance` | 同 §3.1 enum | 该 artifact 的档位；PASS 需 real |
| `verified` | boolean | 是否通过 sha / 布局 / 版本 / `FILES.sha256` 四项复核 |

可选：`bytes`、`manifest_path`、`installer_sha256`、`verification{sha256,layout,version,
files_sha256}`。A 侧文本用 `sha256:<hex>`；P 在写入 install-receipt 前 **strip 前缀**，
所以 `install-receipt.artifact.sha256` 是严格的 64 hex。`build-receipt` 的 artifact 引用
则允许带前缀（A 原样上报，不强迫改写）。

---

## 4. 验收策略（P1.3 实现，P1.1 只声明）

`prodline/pipeline.yaml` 的 `acceptance` 段是唯一声明：

- `report_schema: acceptance-report/v1`；
- `report_fields: [schema, commit, stages, receipts, verdict]`；
- `aggregate_only: true`：`accept.py` 只聚合 receipt 的 `path + sha256`，不重算底层证据；
- `skip_is_not_pass: true`：`SKIP` 不是 `PASS`；
- `pass_requires_provenance: real`：非 real 不给该段 PASS。

P1.3 不得放宽这四条；若要改，按 §1 的变更纪律先改本文件与 `pipeline.yaml`。

---

## 5. v1 演进规则（冻结之后）

1. **只加 optional 字段**：新增字段必须可选，且同步补正样例 + 负样例；
2. **不改语义**：已有字段的 type / enum / required 不得改；
3. **破坏性变更新开 v2**：文件 `schemas/<name>-receipt.v2.schema.json`，
   `$id` 与 `schema` const 都升 v2；`accept.py` 在 v2 就绪前继续只认 v1；
4. **不删字段**：即使没人用，也保留到 v2；
5. **schema 文件的 `$id` 必须唯一**，且与 `pipeline.yaml#receipts.<id>.schema_id` 一致
   （测试会锁）。

---

## 6. 边界（arch 铁律）

| 规则 | 落地 | 验证 |
| --- | --- | --- |
| `prodline/**` 记为 **P** | `arch/architecture.yaml#modules` 的 `prodline` 条目 | `check_boundaries.py` |
| `K/X/G → P = 0` | 产品代码不得 import `prodline` | 同上，6 个 forbidden pair 逐个比较 |
| 产品进程不得 import prodline | 同上；`prodline` 自身只 import stdlib | 同上 + 代码 review |
| baseline 变更必须带理由 | `--update-baseline --reason ...` | baseline 文件内 `reason` 字段 |

本目录是 **P**，允许只读依赖 K/X/G；但“允许”不等于“应该”：P1.1 的 `prodline/__init__.py`
不 import 任何产品模块，保证契约层可被独立加载。

---

## 7. 文件地图

```text
prodline/
├── __init__.py                  # 契约版本常量；无产品 import
├── pipeline.yaml                # 5 stage + 6 receipt 映射 + accept 策略
├── schemas/
│   ├── build-receipt.schema.json
│   ├── install-receipt.schema.json   # 含 artifact{} 必填
│   ├── runtime-receipt.schema.json
│   ├── task-receipt.schema.json
│   ├── recovery-receipt.schema.json
│   └── release-receipt.schema.json
├── stages/
│   ├── __init__.py              # stage 包说明
│   └── source.py                # P1.2：4 条版本线 + tauri(B14) + lockfile 哈希 + CLI
├── runners/__init__.py          # P1.3 填 receipt_check.py / accept.py
└── tests/
    ├── _schema_subset.py        # stdlib schema 子集校验器（测试支持）
    ├── test_pipeline_contract.py
    ├── test_receipt_schemas.py
    └── test_source_stage.py     # source 正样例 + 漂移/漏登/篡改/CRLF 负样例
```

---

## 8. 验证

```bash
cd /home/a7067567g/下载/deepseek/60-外部仓库/gugu-work

# 契约 + schema 正负样例
.venv/bin/python -m pytest prodline/tests -q

# arch：prodline 记为 P，K/X/G→P = 0
.venv/bin/python scripts/arch/check_boundaries.py --baseline arch/baseline.json
```

---

## 9. 历史：P1.1 不做什么（P1.2 已补 source）

P1.1 的边界保持原样记录：

- 不实现 `stages/source.py`（**P1.2 已实现，见 §2.1 / `SOURCE-EVIDENCE.md`**）、
  不实现 `receipt_check.py` / `accept.py`（P1.3）；
- 不生成 `acceptance-report.json`；
- 不定义 `artifact-manifest/v1`（A01 的产物契约，本文只引用）；
- 不跑构建 / 安装 / 真实任务 / soak；
- 不把 fixture 写成 PASS。
