---
schema: gugu.prodline-artifact-contract-doc/v1
status: frozen
owner: area-a
updated: "2026-10-25"
contract_version: 1
---

# A→B artifact 交付契约（A01，冻结）

> 机器可读 schema：[`prodline/contracts/artifact-manifest.v1.json`](../../../prodline/contracts/artifact-manifest.v1.json)。
> 字段语义（人读）：[`prodline/contracts/artifact-manifest.v1.md`](../../../prodline/contracts/artifact-manifest.v1.md)。
> 可执行 checker：[`prodline/gates/artifact_check.py`](../../../prodline/gates/artifact_check.py)。
> 负样例矩阵：[`prodline/tests/fixtures/artifact/`](../../../prodline/tests/fixtures/artifact/)。
> 上位契约：[`prodline/pipeline.yaml`](../../../prodline/pipeline.yaml)、
> `strategy/prodline/LONG-LINE-PLAN-v2.md` §4（strategy 目录不在本仓）。

本文是 A 区（构建交付）交给 B 区（安装运行）的**架构层接口说明**。A01 之后，A02
只负责按契约产出真实 artifact，B01/B02 只负责按契约消费；字段语义不再“按理解实现”。

## 1. 冻结了什么

| 对象 | 位置 | 状态 |
| --- | --- | --- |
| `artifact-manifest/v1` JSON Schema（21 required） | `prodline/contracts/artifact-manifest.v1.json` | **冻结**；改 required/type/enum = v2 |
| 字段语义 / 拒收条件 / B 消费步骤 | `prodline/contracts/artifact-manifest.v1.md` | **冻结** |
| 独立 checker（schema + 字节 + 链 + 三版本 + git_tree + 路径安全） | `prodline/gates/artifact_check.py` | 可执行，退出码 0/1/2 |
| 正/负 fixture 矩阵（21 例，文本生成） | `prodline/tests/fixtures/artifact/` | 每次真跑，无二进制 fixture |
| 6 个 receipt schema | `prodline/schemas/*.schema.json` | **不改字节**（P1 冻结，A01 只加 optional 字段） |

`artifact-manifest` **不注册**为第 7 个 receipt；它是 artifact 的清单，不是回执。

## 2. 交付集合

```text
artifact.zip                 # 扁平根，git 跟踪内容 + 合成 FILES.sha256
artifact-manifest.json       # 与 zip 并列；绝不嵌进 zip（避免自指）
```

zip 根必须有 `pyproject.toml` / `GUGU_VERSION` / `FILES.sha256` /
`scripts/install.ps1`；不得有 `.git/**`、`node_modules/**`、`.venv/**`、
`apps/desktop/{dist,build,release}/**`、`artifacts/**`、`__pycache__/**`。
`excludes` 字段只是给 B 审计；checker 独立扫描 zip，不信自述。

## 3. 三个关键裁决（本轮把歧义钉死）

### 3.1 `FILES.sha256` 是唯一合成成员

`FILES.sha256` 由 A 在构建期生成并放入 zip，因此**不在 commit tree 里**。
格式 `hex␠␠path\n`，按 path 排序、LF、UTF-8、无 BOM；自身不列入，也不进
`files_count` / `files_sha256` 的自指计算。

### 3.2 `git_tree` 重建排除 `FILES.sha256`

`git_tree` = `git rev-parse <commit>^{tree}`。B 侧等价复核：

```bash
unzip -q artifact.zip -d "$DEST"
rm -f "$DEST/FILES.sha256"
( cd "$DEST" && git init -q && git add -A && git write-tree )
# 必须等于 manifest.git_tree
```

checker 把 zip 解到临时目录后跑同样的 git 命令
（`git -c core.autocrlf=false add -A -f` + `git write-tree`，`.gitattributes` 仍生效），
因此与上面的 B 侧命令逐字一致。**不能**用 raw zip 字节自算树：`git archive` 会按
`*.ps1 text eol=crlf` 输出 CRLF，而 commit 内的 blob 是 LF；纯字节重建会把真实
artifact 误判红。支持 100644/100755/120000；symlink 最后创建；目录条目按 git 的
`name/` 排序。仓库当前 15,134 个跟踪文件全为 100644，但实现不假设这一点。

### 3.3 三版本 + tag 从 zip 内文件复算

| manifest | zip 来源 |
| --- | --- |
| `product_version` | `GUGU_VERSION` |
| `upstream_kernel_version` | `pyproject.toml` `[project].version` |
| `desktop_version` | `apps/desktop/package.json#version` |
| `upstream_tag` | `arch/upstream.lock#tag` |

根 `package.json#version`（1.0.0）不进 manifest：它是 npm workspace 根，不是产品线。
NSIS 产物名用 `desktop_version`（0.17.6），不是 `product_version`（0.9.0）。

## 4. provenance 与 PASS

- `provenance ∈ {real,fixture,mock,accelerated}`；只有 `real` 可能成为 PASS。
- 非 `real` 的 manifest **必须**带非空 `fixture_reason`（schema 子集没有 `if/then`，
  所以这是 checker 的语义检查）。
- 与 6 个 receipt 的规则一致：fixture/mock/accelerated 证据单列，不得进入 PASS 行。

## 5. B 侧消费 runbook（照抄）

```bash
# 0) 目录里只应有这两份并列文件
ls artifact.zip artifact-manifest.json

# 1) 字节身份
sha256sum artifact.zip      # 去掉 "sha256:" 前缀后与 manifest.artifact_sha256 相等

# 2) schema + 语义 + 字节 + FILES 链 + 三版本 + git_tree + 路径安全
python -m prodline.gates.artifact_check \
  --zip artifact.zip --manifest artifact-manifest.json
# exit 0 才继续；1 = 契约/字节问题；2 = 输入/配置错误

# 3) 解压后逐文件复算（可选，checker 已经从 zip 做过一遍）
unzip -q artifact.zip -d "$DEST"
( cd "$DEST" && sha256sum -c FILES.sha256 --quiet )

# 4) 安装时把 artifact_sha256 / files_sha256 记进 install-receipt.artifact{}
```

**拒收条件**（任一成立即停）：`dirty != false`、`commit` 不符、
`git_tree` 不符、`includes` 缺三项、zip 字节数/sha 不符、`files_count` 不符、
`FILES.sha256` 复算失败、逐文件 sha 不符、zip-slip/绝对路径/盘符/反斜杠、
根缺 `GUGU_VERSION`/`pyproject.toml`、出现 §2 的禁止树。

## 6. A 侧产出义务（A02）

1. 干净 commit 上 `git archive --format=zip -9 HEAD` 得基础归档；
2. 生成 `FILES.sha256`，以确定性方式加入 zip（排序、固定 metadata）；
3. 写 manifest 的 21 个字段；`built_at` 只供人读；
4. 跑 `artifact_check` 必须 exit 0；**不得**用 `--no-verify-*` 伪造通过；
5. 两个 commit 各产一次 artifact（B05 的 from/to），并各自带 build-receipt；
6. 把 `artifact.zip`/`artifact-manifest.json`/`build-receipt.json` 放同目录交付。

### 6.1 A02 实现口径（Linux，已落地 2026-10-25）

实现：`prodline/stages/build.py`（`provenance=real`，无 fixture/mock 开关）。

```bash
# 1) 同一 commit 的 source evidence（accept 的 source 段要它）
.venv/bin/python -m prodline.stages.source --root . --out artifacts/source-evidence.json --quiet

# 2) 构建（默认双构建；输出目录即交付目录）
.venv/bin/python -m prodline.stages.build --root . --outdir artifacts/receipts

# 3) 全验（默认 verify_files + verify_git_tree + repo commit->tree + receipt 哈希）
.venv/bin/python -m prodline.gates.artifact_check \
  --zip artifacts/receipts/artifact.zip \
  --manifest artifacts/receipts/artifact-manifest.json \
  --build-receipt artifacts/receipts/build-receipt.json --repo .

# 4) 验收（source PASS + build PASS，provenance=real）
.venv/bin/python -m prodline.runners.accept --only source,build --expect-pass \
  --out artifacts/receipts/acceptance-report.json
```

| 决定 | 实现 | 原因 |
| --- | --- | --- |
| 交付目录 | `artifacts/receipts/` | 契约要求 zip/manifest/receipt 同目录；`accept` 默认递归扫 `artifacts/receipts/**/*.json`，一个目录同时满足两者。该目录被 `.gitignore`（`/artifacts/`）忽略，不污染工作树。 |
| `FILES.sha256` 的哈希对象 | **zip 成员字节**（`git archive` 之后） | checker 用 `archive.read(path)` 复算；`*.ps1` 在 archive 里是 CRLF、blob 是 LF，用工作树字节会全红。 |
| zip 重建 | 成员按 path 排序、`date_time=(1980,1,1,0,0,0)`、`create_system=3`、`100644<<16`、deflate 9 | 同一 commit 两次构建（含跨进程）zip sha256 相同；成员内容字节原样复制，FILES 链不变。 |
| `dirty` | `git status --porcelain --untracked-files=no` | manifest schema `dirty` 是 `const false`；未跟踪的交付物（`artifacts/**`）不允许把构建判脏，但**一个 tracked 改动即拒绝构建**。 |
| 链方向 | receipt → zip；manifest → receipt | 若 receipt 再写 `manifest_sha256`，`H(manifest)` 与 `H(receipt)` 互相依赖，无解。故 receipt 的 `artifacts[]` 只有 zip（不用可选 `manifest_sha256`），manifest 的 `build_receipt_sha256` 指向最终 receipt。manifest 自身由 checker 的 zip/commit/版本/git_tree 全验兜底。 |
| 可复现档位 | `mode=byte-identical`、`verdict=equal`，`builds[]` 两轮 | 完整跑两遍 `git archive`→FILES→zip；只要 sha/bytes/files_sha 有一项不同即 `status=fail`、CLI exit 1。**不存在**“单构建也 PASS”的开关。 |
| 版本字段 | 全部从 zip 内文件读 | `GUGU_VERSION` / `pyproject.toml` / `apps/desktop/package.json` / `arch/upstream.lock`，与 checker 同源。 |
| lockfiles | 从 zip 成员复算 | 与 checker 的 `lockfiles` 检查逐字节一致；source evidence 的 canonical 哈希在 LF 锁文件上相同。 |
| `artifact-check.json` | 独立证据，不是 receipt、不进 receipt 链 | 它的内容包含 manifest sha/receipt sha；若写进 receipt 会重新引入循环。 |
| `require_source_evidence` | 默认关闭；CI 可显式打开 | 默认缺/陈旧 source evidence 时不伪造 `evidence[]`，只在 receipt 里记 `omitted` 原因；显式打开则硬失败。 |
| 跨机可复现 | **不声明** | 只声明同 runner/zlib 内 byte-identical；跨机差异留给 A03/A04 的 runner 矩阵。 |

A02 是“当前 commit 一次真实交付”。契约 §6.5 的“两个 commit 各产一次”仍是 B05
（from/to upgrade）的义务：对每个 commit 重复上面的第 1–4 步，各自带自己的
build-receipt；不要把两次同 commit 的双构建误记成两个 commit 的产物。

## 7. 负样例矩阵

`prodline/tests/fixtures/artifact/cases.json` 当前 21 例（1 正 + 20 负），每次测试
从 `tree/base` 重建 zip/manifest。**强制四类**（测试会断言存在）：

| 类 | case |
| --- | --- |
| 缺字段 | `missing-git-tree` |
| dirty | `dirty-true` |
| 三版本不等 | `version-mismatch`（另有 `kernel-version-mismatch`） |
| files_count 不符 | `files-count-mismatch` |

其余覆盖：artifact sha/bytes、files sha、多余/缺失/未列成员、单文件 sha、zip-slip、
绝对路径、`.git/**`、git_tree 不符、fixture 无 reason、runner 缺键、producer/schema
不符、FILES 自列。`valid` 必须绿。

## 8. 源文档冲突（已裁决）

| 文档 | 旧口径 | 处理 |
| --- | --- | --- |
| `AREA-CONTRACT.md` §1 | 8 字段 | 作废；按 21 |
| `plan-A.md` §5.3 | 8 字段 + “新增” | 作废；21 全 required |
| `LONG-LINE-PLAN-v2.md` §4.2/M1/M3 | 21 字段、两份并列、git_tree/provenance | **采用** |
| `ROUND-SPEC.md` “见 plan-A §5.3” | 旧指向 | pipeline owner 修文；实现以本契约为准 |

## 9. 未完成项（按轮更新；不写成 PASS）

| 项 | 状态 | 去向 |
| --- | --- | --- |
| 真实 `artifact.zip` + manifest + build-receipt（Linux，当前 commit） | **已产出**（A02；`artifact_check` exit 0） | 证据见 `runs/gugu-pl-a02-linux-build/reports/` |
| `accept --only source,build` 的 build 段 | **PASS**（A02；source PASS + build PASS，均 provenance=real） | 已闭环 |
| P1 G-1（accept 不比较 receipt.commit）、G-2（accept 不验链） | **已修**（P1b；accept 默认校验 commit + 链） | 已闭环 |
| `pipeline.yaml#status` 仍 `p1-draft` | **未翻**（G-3） | pipeline owner |
| Windows artifact / NSIS / build-win job | **未做** | A03/A04 |
| 两个 commit 的 artifact（B05 from/to） | **未做**（A02 只产当前 commit） | B05 |
| 跨机 byte-identical（不同 runner/zlib） | **未验证**（不声明） | A03/A04 runner 矩阵 |
| A06 payload 分区 | 未做 | A06 |

## 10. 如何复核本冻结

```bash
cd /path/to/gugu-work
export PYTHONDONTWRITEBYTECODE=1

.venv/bin/python -m pytest prodline/tests/fixtures/artifact -q
.venv/bin/python -m pytest prodline/tests -q
.venv/bin/python -m py_compile prodline/gates/artifact_check.py
.venv/bin/python - <<'PY'
import json
from prodline.schema_subset import assert_supported_schema
s = json.load(open("prodline/contracts/artifact-manifest.v1.json"))
assert len(s["required"]) == 21
assert_supported_schema(s)
print("artifact-manifest/v1: 21 required, subset ok")
PY
```

checker 的 `--no-verify-files` / `--no-verify-git-tree` 只用于调试；A02/B 的 PASS
必须用默认（全验）跑法。

## 11. 变更策略

- v1 只加 optional 字段；改 required/type/enum/const = 新开 `artifact-manifest/v2`。
- 消费方在 v2 就绪前只认 v1，不得接受缺字段的 v1。
- 6 个 receipt schema 的字节不动；需要新语义时走 `receipt_check` 的 gate 层规则。
