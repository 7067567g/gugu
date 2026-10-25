---
schema: gugu.prodline-acceptance-gate-doc/v1
status: active
owner: release-manager
updated: "2026-10-25"
contract_version: 1
---

# P1 gate 层：receipt_check + accept + CI prodline-gate（P1.3）

> 实现：[`prodline/gates/receipt_check.py`](../../../prodline/gates/receipt_check.py)、
> [`prodline/runners/accept.py`](../../../prodline/runners/accept.py)、
> [`prodline/gates/arch_boundaries.py`](../../../prodline/gates/arch_boundaries.py)。
> 固定契约见 [`OWNERSHIP.md`](OWNERSHIP.md) 与
> [`prodline/pipeline.yaml`](../../../prodline/pipeline.yaml)；
> CI 见 [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml)。

本层只做三件事：**校验 receipt、聚合 receipt、把门接进 CI**。它不生产
build/install/runtime/quality 的真实证据（A/B 区才生产），也不冻结 schema
（P1.4 冻结）。

## 1. 状态判定（accept 的核心）

`accept.py` 对每个 `--only` 指定的 stage 产出一个 `pass|fail|skip`：

| 输入情况 | stage 状态 | 说明 |
| --- | --- | --- |
| receipt `status: pass` 且 `provenance: real` | `PASS` | 唯一能 PASS 的组合 |
| receipt `status: pass`，provenance 为 `fixture` / `mock` / `accelerated` | `SKIP` | 形状合法，但不能证明验收 |
| receipt `status: skip` | `SKIP` | SKIP ≠ PASS |
| 该 stage 没有任何 receipt | `SKIP` | 未实现 / 未产出，不假绿 |
| receipt `status: fail` | `FAIL` | fail 优先于同 stage 的其它 receipt |
| receipt 校验失败（schema / 语义 / 链 / commit / mixed-commit） | `FAIL` | 篡改或坏证据不能让段通过 |
| source evidence `status: pass`、`provenance: real`、commit 一致 | `PASS` | 只聚合，见 §3 |
| source evidence 缺失 / provenance 非 real / status skip | `SKIP` | 同上 |
| source evidence 形状坏 / status fail / commit 不一致 | `FAIL` | |

report 的 `verdict`：

| verdict | 条件 | accept 退出码 |
| --- | --- | --- |
| `pass` | 所有被请求 stage 都是 `PASS` | 0 |
| `incomplete` | 至少一个 `SKIP`，且没有 `FAIL` | 0（P1 契约：未实现段允许 exit 0） |
| `fail` | 任一 stage `FAIL`，或 receipt 目录加载失败 | 1 |

CI 不能依赖“incomplete 也 exit 0”，所以 `prodline-gate` 用
`--expect-pass`：只要 verdict 不是 `pass` 就 exit 1。命令行用法错误（未知
stage、坏 `--commit`）exit 2。

## 2. `receipt_check.py`

```bash
python -m prodline.gates.receipt_check --receipts-dir artifacts/receipts
python -m prodline.gates.receipt_check path/to/build-receipt.json --verify-files --root .
python -m prodline.gates.receipt_check --receipts-dir artifacts/receipts --format json
```

检查项：

1. **schema**：`schema` 字段必须命中 `pipeline.yaml#receipts` 的 6 个
   `*-receipt/v1` 之一；按该 schema 做 draft-2020-12 子集校验（stdlib 实现，
   不引入 `jsonschema`）。
2. **stage 一致**：receipt 的 `stage` 必须等于 registry 声明的生产 stage。
3. **语义必填**（schema 保持 additive-only v1，所以放在 gate 层）：
   - `task-receipt/v1`：`task_id` + `input_hash` + `output_hash` + `verify` + `verdict`；
   - `recovery-receipt/v1`：`fault` + `injected` + `recovered` + `duplicate_side_effects`；
   - `release-receipt/v1`：`from_artifact` + `to_artifact` + `upgrade_ok` + `rollback_ok`。
4. **commit**：给了 `--commit` 就逐份比对；扫描模式默认拒绝 mixed commits
   （`--allow-mixed-commits` 可显式放开）。
5. **sha256 链**（可选 `--verify-files`）：`evidence[]`、build `artifacts[]`
   （含 `manifest_path`）、install `artifact{}`、release `from/to_artifact` 的
   path→sha256 复算；文件缺失或 1 字节篡改都报错。
6. **目录过滤**：只把 `schema` 命中注册表的 JSON 当 receipt；
   `artifact-manifest.json` / source evidence / 上一次的 `acceptance-report.json`
   会被忽略。文件名像 receipt 但 JSON 坏、或 schema 未注册但形如
   `*-receipt/vN`，会报错（不静默放行）。

JSON 输出 schema 为 `gugu.prodline-receipt-check/v1`（工具输出，不是第 7 个
receipt）。退出码：0 全绿 / 1 有问题 / 2 输入或配置错误（含“没有东西可检”）。

## 3. source 证据：`aggregate_only` 的显式特例

`pipeline.yaml#acceptance.rules.aggregate_only=true` 要求聚合器不重算底层
证据。source 没有 receipt，`accept.py` 对
`artifacts/source-evidence.json` 只做：

- 读文件并算**文件本身**的 sha256（进 report 的 `evidence[]`）；
- 形状检查：`schema=gugu.prodline-source-evidence/v1`、`stage=source`、
  `commit` 与 report 一致、`provenance`/`status` 枚举、两个 lockfile 条目有
  sha256/raw_sha256、`problems` 是数组；
- `status=pass` 且 `provenance=real` 才给 source 段 PASS。

**不重算**版本线与 lockfile 哈希。需要复算时显式调用
`prodline.stages.source.verify_source_evidence(evidence, root)`（A02 使用）。
这一条是本层唯一的“聚合 + 形状检查”特例，不得扩展成 accept 内重算。

## 4. `acceptance-report.json`

字段严格为 `{schema, commit, stages, receipts, verdict}`（`pipeline.yaml`
的 `report_fields`）。示例（只跑 source）：

```json
{
  "schema": "acceptance-report/v1",
  "commit": "dfa3606bfb442a2958b2f34b8c7e13c2baf07f1c",
  "stages": [
    {
      "id": "source",
      "status": "pass",
      "provenance": "real",
      "reason": "source evidence status=pass provenance=real",
      "evidence": [
        {
          "path": "artifacts/source-evidence.json",
          "sha256": "…",
          "schema": "gugu.prodline-source-evidence/v1",
          "status": "pass",
          "provenance": "real"
        }
      ]
    }
  ],
  "receipts": [],
  "verdict": "pass"
}
```

`receipts[]` 每条含 `path + sha256`（以及 schema/stage/status/provenance），
按 stage 顺序排列。stage 条目额外带 `reason`、`receipts[]` 或 `evidence[]`——
报告顶层仍是契约的 5 个字段。

## 5. CI 接线

`.github/workflows/ci.yml` 的 `prodline-gate`（Python 3.12，装既有 pin
`pyyaml==6.0.3` + `pytest==9.1.1`，不装产品依赖）：

1. `python -m pytest prodline/tests -q`；
2. `python -m prodline.stages.source --root . --out artifacts/source-evidence.json`；
3. `python -m prodline.runners.accept --only source --out artifacts/receipts/acceptance-report.json --expect-pass`。

该 job 无 soft-fail，且是 `all-checks-passed.needs` 的成员。

`arch-gate` 同时收口 r03 backlog：

| 项 | 内容 | 落点 |
| --- | --- | --- |
| F1 / B1 | `tests/ci` 的 `REQUIRED_JOBS` 未含 `arch-gate` | `tests/ci/test_ci_workflow_wiring.py` |
| F2 / B2 | 首方 `parse_errors > 0` 未 gate | arch-gate 写 `--json-out`，再由 `prodline/gates/arch_boundaries.py` 消费；exit 2 + 显式 `--allow`，默认空、样例不足时 fail-closed |
| F4 / B3 | `pytest scripts/arch` 未进 CI | arch-gate 增加该 step（同 pin 的 pytest） |

**F2 的边界说明**：本轮 scope 不含 `scripts/arch/**`，所以没有把
`parse_errors` 的 exit 2 写进 checker 本体；CI 层 gate 只消费 checker 的 JSON
回执，规则仍只有 checker 一份。checker 本体的 exit code 语义留给其 owner。

## 6. 不做什么

- 不生产真实 build/install/runtime/quality receipt；
- 不冻结 schema（`pipeline.yaml#status` 仍是 `p1-draft`，P1.4 冻结）；
- 不把 fixture/mock/accelerated 写成 PASS；
- 不改产品代码 / `pyproject.toml` / `scripts/install.*`；
- 不 push / tag / publish。
