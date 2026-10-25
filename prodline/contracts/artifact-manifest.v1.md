---
schema: gugu.prodline-artifact-manifest-doc/v1
status: frozen
owner: area-a
updated: "2026-10-25"
contract_version: 1
---

# artifact-manifest/v1 — A→B 交付清单契约（冻结）

> 机器可读 schema：[`prodline/contracts/artifact-manifest.v1.json`](artifact-manifest.v1.json)。
> 可执行 checker：[`prodline/gates/artifact_check.py`](../gates/artifact_check.py)。
> 负样例矩阵：[`prodline/tests/fixtures/artifact/cases.json`](../tests/fixtures/artifact/cases.json)。
> 架构层说明：[`docs/architecture/prodline/ARTIFACT-CONTRACT.md`](../../docs/architecture/prodline/ARTIFACT-CONTRACT.md)。
> 上位契约：[`prodline/pipeline.yaml`](../pipeline.yaml)、
> `strategy/prodline/LONG-LINE-PLAN-v2.md` §4.2（strategy 目录不在本仓，引用为文本）。

本文件冻结 **A 区交给 B 区的唯一 artifact 契约**。后续轮次（A02 产出、B01/B02 消费）
只实现，不再解释字段语义；有分歧时以本文件 + JSON Schema 为准。

## 1. 交付集合与布局

artifact 集合 = **两份并列文件**，manifest **不嵌进 zip**（`artifact_sha256` 是 zip
自身哈希，嵌入会自指）：

```text
artifact.zip
artifact-manifest.json
```

`artifact.zip` 是**扁平根**（无顶层目录包装），根必须有：

```text
GUGU_VERSION
pyproject.toml
FILES.sha256
scripts/install.ps1
```

不得包含：`.git/**`、`node_modules/**`、`.venv/**`、
`apps/desktop/{dist,build,release}/**`、`artifacts/**`、`__pycache__/**`。
`git archive` 天然满足大部分排除；checker 会独立拒绝，不依赖 manifest 的 `excludes` 自述。

## 2. 21 个必填字段

`required` 恰为下表 21 个；`additionalProperties: true`，只允许新增 optional 字段。
sha 文本统一 `sha256:<64 lowercase hex>`；`commit` / `git_tree` 为 40 hex；
`built_at` 为带时区 ISO-8601，**不参与任何相等/复现判定**。

| # | 字段 | 类型/约束 | 语义 |
| --- | --- | --- | --- |
| 1 | `schema` | const `artifact-manifest/v1` | 文档版本 |
| 2 | `commit` | 40 hex | zip 内容对应的 commit |
| 3 | `git_tree` | 40 hex | `git rev-parse <commit>^{tree}`；见 §4 |
| 4 | `provenance` | `real/fixture/mock/accelerated` | 证据性质；非 real 必须有 `fixture_reason`，且永不 PASS |
| 5 | `product_version` | semver | = zip 内 `GUGU_VERSION`（0.9.0） |
| 6 | `desktop_version` | semver | = zip 内 `apps/desktop/package.json#version`（0.17.6），NSIS 产物名用它 |
| 7 | `upstream_kernel_version` | semver | = zip 内 `pyproject.toml#[project].version`（0.21.5） |
| 8 | `upstream_tag` | `v<semver>` | = zip 内 `arch/upstream.lock#tag`（v0.22.5） |
| 9 | `artifact_sha256` | `sha256:<hex>` | zip 字节的 sha256 |
| 10 | `artifact_bytes` | integer ≥ 1 | zip 字节数 |
| 11 | `files_sha256` | `sha256:<hex>` | `FILES.sha256` 文本的 sha256 |
| 12 | `files_count` | integer ≥ 1 | `FILES.sha256` 行数，必须等于实际行数 |
| 13 | `files_manifest` | const `FILES.sha256` | 清单文件名 |
| 14 | `built_at` | date-time | 人类可读；**不参与判定** |
| 15 | `runner` | object | 必含 `os,image,arch,python,uv,node,npm,git` 八个非空字符串 |
| 16 | `dirty` | const `false` | `true` 永不发布；schema 直接判红 |
| 17 | `lockfiles` | object | 必含 `uv.lock` 与 `package-lock.json` 两个 `sha256:` 值；checker 从 zip 内同名文件复算 |
| 18 | `includes` | object | 必含 `pyproject.toml/GUGU_VERSION/scripts/install.ps1` 且均为 `true` |
| 19 | `excludes` | string[]，≥1，unique | 排除 glob，供 B 审计 |
| 20 | `build_receipt_sha256` | `sha256:<hex>` | 对应 `build-receipt/v1` 的 sha256；给 `--build-receipt` 时复算 |
| 21 | `producer` | object | 固定 `{stage:"build", tool:"prodline/stages/build.py", contract_version:1}` |

**允许的 optional 字段**（只能加，不能改上面语义）：
`commit_short`、`branch`、`git_archive{format,compression_level,generator,git_version}`、
`extra_artifacts[]`、`payloads[]`（A06）、`fixture_reason`。

## 3. `FILES.sha256`（唯一合成成员）

- 格式：`<64 hex><SP><SP><relpath><LF>`；按 path 排序；UTF-8、无 BOM、只用 LF。
- 只列 zip 内除自身外的所有普通文件；自身不列入。
- `files_count` = 它的行数；`files_sha256` = 它的**文本字节**的 sha256（不是逐行哈希的再哈希）。
- 它是构建期合成文件，**不在 commit tree 里**。zip 内其余成员必须与 commit tree 逐字/逐 mode 相同。
- checker 会：复算 `FILES.sha256` 文本哈希、行数、每行 path 与 zip 成员集合相等、
  逐文件 sha256 相等；任何多一个文件、少一个文件、1 字节篡改都红。

## 4. `git_tree` 与 commit 绑定

`git_tree` 是 commit 的树，不是 zip 字节的哈希。因为 `FILES.sha256` 是唯一合成成员，
重建树时**排除它**：

```bash
# B 侧等价复核（解压后）
unzip -q artifact.zip -d "$DEST"
rm -f "$DEST/FILES.sha256"
( cd "$DEST" && git init -q && git add -A && git write-tree )
# 输出必须等于 manifest.git_tree
```

A 侧（或 B 侧有完整 checkout 时）还可验证 commit→tree：

```bash
git rev-parse "<commit>^{tree}"   # 必须等于 manifest.git_tree
```

`artifact_check.py` 直接对 zip 重建 git tree（不落盘），规则：
普通文件 mode `100644`；可执行 `100755`；symlink `120000`；目录条目忽略；
排序按 git 的 tree 排序（目录名视作带 `/`）。仓库当前 `git ls-files -s` 全为
`100644`，但 checker 仍支持后两种 mode。

## 5. 三版本 / tag 的字节绑定

manifest 不能自说自话。checker 从 zip 读取并逐字比较：

| manifest 字段 | zip 内来源 | 解析方式 |
| --- | --- | --- |
| `product_version` | `GUGU_VERSION` | 去首尾空白后的全文 |
| `upstream_kernel_version` | `pyproject.toml` | `tomllib` 的 `project.version` |
| `desktop_version` | `apps/desktop/package.json` | JSON `version` |
| `upstream_tag` | `arch/upstream.lock` | JSON `tag` |

根 `package.json#version`（1.0.0）**不进 manifest**：它是 npm workspace 根版本，
不是产品/内核/桌面版本线。

## 6. provenance 与 PASS

- `provenance: real` 才可能成为 PASS；`fixture/mock/accelerated` 只能证明形状。
- 非 `real` 时 gate 要求 `fixture_reason` 非空；`real` 的 manifest 不应带该字段。
- 与 6 个 receipt 的 provenance 规则一致：fixture/mock 不得写进任何 PASS 行。

## 7. B 侧消费步骤（照抄）

```bash
# 1) 身份：zip 字节必须等于 manifest 声明
sha256sum artifact.zip
#    与 manifest.artifact_sha256（去掉 sha256: 前缀）逐字相等

# 2) schema + 语义 + 字节 + 链 + 三版本 + git_tree + 路径安全
python -m prodline.gates.artifact_check \
  --zip artifact.zip --manifest artifact-manifest.json

# 3) 退出码 0 才允许继续；1 = 契约/字节问题；2 = 输入/配置错误
# 4) 解压后（可选）再跑逐文件复算
unzip -q artifact.zip -d "$DEST"
( cd "$DEST" && sha256sum -c FILES.sha256 --quiet )

# 5) 安装时把 artifact_sha256 / files_sha256 记进 install-receipt.artifact{}
```

**拒收条件**（任一成立即停，不许“先装再说”）：
`dirty != false` / `commit != 期望` / `git_tree` 重建不符 / `includes` 缺三项 /
`artifact_sha256` 或 `artifact_bytes` 不符 / `files_count` 不符 / `FILES.sha256` 复算失败 /
逐文件 sha 不符 / zip-slip 条目 / 根缺 `GUGU_VERSION` 或 `pyproject.toml` /
出现 `.git/**`、`node_modules/**`、`.venv/**`、`apps/desktop/{dist,build,release}/**`、`artifacts/**`。

## 8. producer 义务（A02 实现清单）

1. 在干净 commit 上 `git archive --format=zip -9 HEAD` 得基础归档；
2. 生成 `FILES.sha256`，以**确定性**方式加入 zip（排序、固定时间戳/metadata）；
3. 计算 zip 的 `artifact_sha256`/`artifact_bytes`、`FILES.sha256` 的 `files_sha256`/`files_count`；
4. 从 checkout 读三版本 + `arch/upstream.lock#tag`，逐字写进 manifest；
5. 写 `runner` 八项、`lockfiles` 两项、`includes` 三项、`excludes`、`build_receipt_sha256`、`producer`；
6. 跑 `artifact_check` 必须 exit 0；两个 commit 各产一次（B05 的 from/to）。

## 9. 源文档冲突的裁决（不再保留两种读法）

| 文档 | 旧口径 | 本轮裁决 |
| --- | --- | --- |
| `AREA-CONTRACT.md` §1 | 8 个固定字段 | **作废**；按本文件 21 字段 |
| `plan-A.md` §5.3 | 8 字段 + 若干“新增”字段 | **作废**；21 字段全 required |
| `LONG-LINE-PLAN-v2.md` §4.2 / M1 / M3 | 21 字段、两份并列、`git_tree`+`provenance` | **采用** |
| `ROUND-SPEC.md` “manifest 字段见 plan-A §5.3” | 指向旧口径 | 由 pipeline owner 修文；实现以本文件为准 |

`FILES.sha256` 与 `git_tree` 的冲突（新发现）按 §3/§4 裁决：合成成员排除在树重建之外。

## 10. 变更策略

- `v1` 只加 optional 字段；`rename/retype/删除 required/改 enum/改 const` = 破坏性变更，
  必须新开 `artifact-manifest/v2`（`$id` 与 `schema const` 同步升 v2）。
- 消费方（B01/B02）在 v2 就绪前只认 v1；不得为兼容而接受缺字段的 v1。
- 本 schema 不注册进 `pipeline.yaml#receipts`：它不是第 7 个 receipt。

## 11. 本轮边界 / 未完成

- 本轮**不产出**真实 `artifact.zip` / `artifact-manifest.json`（A02）；checker 只用
  fixture 矩阵证明会红/会绿。
- P1 的 G-1（accept 不比较 receipt.commit）、G-2（accept 不验链）不在本轮 scope；
  A02 仍须先跑 `receipt_check --verify-files --commit <sha>`。
- Windows 产 artifact / NSIS 产物 / CI job 属 A03/A04。
