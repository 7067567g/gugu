---
schema: gugu.prodline-source-evidence-doc/v1
status: active
owner: release-manager
updated: "2026-10-25"
contract_version: 1
---

# source 阶段证据契约（P1.2）

> 实现：[`prodline/stages/source.py`](../../../prodline/stages/source.py)。
> 固定 stage/receipt 契约见 [`OWNERSHIP.md`](OWNERSHIP.md) 与
> [`prodline/pipeline.yaml`](../../../prodline/pipeline.yaml)。
>
> **这不是第 7 个 receipt。** `pipeline.yaml` 的 6 个 receipt（build/install/runtime/task/
> recovery/release）不变；source 证据是 build 的输入，不是 receipt，也不进
> `prodline/schemas/`。

## 1. 做什么 / 不做什么

`source` 阶段只读：

1. 读 `version-sources.json`，断言 **4 条发布版本线**（product / upstream_kernel /
   upstream / upstream_desktop）与 checkout 一致；
2. 登记并断言 **B14** 的 Tauri bootstrap installer 版本线
   （`apps/bootstrap-installer/src-tauri/tauri.conf.json`）；
3. 对 `uv.lock` / `package-lock.json` 计算 canonical sha256（UTF-8、LF、无 BOM）
   与 raw sha256；
4. 产出一份 JSON 证据（`gugu.prodline-source-evidence/v1`），交给 build（A02）。

它**不**构建、不安装、不改 checkout、不写 receipt、不访问网络、不跑 git 写操作
（只 `git rev-parse HEAD`）。

## 2. 公共接口（A02 消费）

```python
from pathlib import Path

from prodline.stages.source import (
    EVIDENCE_SCHEMA,          # "gugu.prodline-source-evidence/v1"
    LOCKFILES,                # ("uv.lock", "package-lock.json")
    RELEASE_LINE_GROUPS,      # 4 个发布线 group
    collect_source_evidence,  # -> dict；strict=True 时漂移抛 SourceAssertionError
    hash_lockfile,            # -> dict；canonical/raw sha256 + bytes + eol/bom
    verify_source_evidence,   # -> list[str]；重读并复算，返回每处不一致
    version_line_problems,    # -> list[str]；只查版本线/manifest
)
```

### `collect_source_evidence(root=None, *, commit=None, strict=True)`

- `root` 默认是本仓库根（`prodline/stages/source.py` 向上两级）。
- `commit` 默认 `git rev-parse HEAD`；测试/无 `.git` 归档可显式传入 40 hex。
- `strict=True`：有任何问题就抛 `SourceAssertionError`，异常的 `.problems` 是完整清单，
  `.evidence` 是失败态证据（`status="fail"`），便于落盘后仍可审计。
- `strict=False`：不抛，返回 `status="fail"` + `problems` 的证据。
- 缺少 manifest / 版本文件 / lockfile、JSON/TOML 坏掉、commit 不是 40 hex 都会变成
  可读的 `SourceError` 或 problem，**不会静默跳过**。

### `verify_source_evidence(evidence, root=None)`

把已落盘的证据与当前 checkout 重读比对：

- 版本文件当前值 vs 证据 `versions.<group>.actual`；
- manifest 当前断言 vs 当前值（`version_line_problems`）；
- 两个 lockfile 的 `sha256` / `raw_sha256` / `bytes`。

**篡改 1 字节必然红**：lockfile 任一字节变化都会改变 `sha256`（即使 canonical 化后
也变化）。返回空 list 才表示证据仍可复算。

### `hash_lockfile(path, root=None)`

返回：

| 字段 | 语义 |
| --- | --- |
| `path` | 相对 `root` 的 POSIX 路径（不在 root 下则用绝对路径） |
| `bytes` | 原文件字节数 |
| `sha256` | **canonical** 字节（UTF-8、LF、无 BOM）的 `sha256:<hex>`；A02 用这个 |
| `raw_sha256` | 原文件字节的 `sha256:<hex>`；传输/落盘完整性可用 |
| `canonicalization` | `"utf-8, LF, no BOM"` |
| `canonical` | 磁盘文件本身是否已是 canonical（BOM/CRLF 都为 false） |
| `eol` | `lf` / `crlf` / `cr` |
| `bom` | 是否有 UTF-8 BOM |

为什么同时保留两个哈希：A02 契约写的是 canonical（LF、无 BOM、按字节），但
Windows checkout 可能 CRLF；canonical 哈希让同一 lockfile 跨 checkout 可复算，
`raw_sha256` 则保留“实际收到的字节”证据，二者不互相替代。

## 3. 证据字段

```json
{
  "schema": "gugu.prodline-source-evidence/v1",
  "stage": "source",
  "commit": "<40 hex>",
  "generated_at": "2026-10-25T11:09:01Z",
  "provenance": "real",
  "status": "pass",
  "root": "/abs/path/to/gugu-work",
  "release_lines": ["product", "upstream_kernel", "upstream", "upstream_desktop"],
  "versions": {
    "<group>": {
      "path": "GUGU_VERSION",
      "pointer": "text",
      "expected": "semver",
      "actual": "0.9.0",
      "verified": true
    }
  },
  "lockfiles": {
    "uv.lock": {"path": "uv.lock", "bytes": 735544, "sha256": "sha256:…",
                "raw_sha256": "sha256:…", "canonicalization": "utf-8, LF, no BOM",
                "canonical": true, "eol": "lf", "bom": false}
  },
  "problems": []
}
```

- `schema/stage/commit/generated_at/provenance/status` 借用 receipt 身份字段，
  让验收层可统一处理；但 `schema` 不是 6 个 receipt 之一。
- `release_lines` 固定 4 条；`bootstrap_installer_tauri` 在 `versions` 中，但**不在**
  `release_lines` 里——它是 B14 登记的辅助源，不是发布版本线。
- `workspace_unclassified`（根 `package.json`）仍按 `unverified` 登记，不参与相等断言。
- `status` 只看 `problems` 是否为空；`provenance` 恒为 `real`（读的是真实 checkout）。

## 4. 版本线口径

| group | 文件/指针 | 断言 | 说明 |
| --- | --- | --- | --- |
| `product` | `GUGU_VERSION`（`text`） | `semver` | 产品版本，每次发布动态读取 |
| `upstream_kernel` | `pyproject.toml#project.version` | 字面量 `0.21.5` | 上游 kernel，不由 gugu 发布改写 |
| `upstream` | `arch/upstream.lock#tag` | 字面量 `v0.22.5` | 真上游基线；同时校验 lock 内 `commit == expected_commit` 且 40 hex |
| `upstream_desktop` | `apps/desktop/package.json#version` | 字面量 `0.17.6` | 桌面壳独立版本线 |
| `bootstrap_installer_tauri` | `apps/bootstrap-installer/src-tauri/tauri.conf.json#version` | 字面量 `0.21.1` | **B14**：Tauri 引导安装器自己的版本线，独立于上面 4 条 |

**为什么 tauri 是字面量而不是“等于 kernel”**：实测 tauri=`0.21.1`、kernel=`0.21.5`、
desktop=`0.17.6`，没有任何文档规定三者必须相等。强行相等是编造语义；字面量断言至少
能在它漂移时变红，同时不制造假约束。

## 5. 负样例（已真跑）

`prodline/tests/test_source_stage.py` 覆盖：

| 样例 | 期望 |
| --- | --- |
| 版本线漂移（desktop `0.17.6 → 9.9.9`） | `SourceAssertionError`；`strict=False` 时 `status=fail` |
| tauri 漏登（manifest 少 group） | 红，problem 点名 `bootstrap_installer_tauri` |
| tauri 版本漂移 | 红，problem 点名 `tauri.conf.json` |
| `arch/upstream.lock#commit != expected_commit` | 红 |
| 缺 `version-sources.json` | 红 |
| 缺 `package-lock.json` | 红 |
| lockfile 篡改 1 字节后 `verify_source_evidence` | 返回 `uv.lock: sha256 changed …` |
| lockfile 写成 CRLF / 带 BOM | canonical 哈希与 LF 版本相同，`canonical=false` |

## 6. CLI

```bash
cd /home/a7067567g/下载/deepseek/60-外部仓库/gugu-work

# 写文件（成功 exit 0；断言失败 exit 1；输入不可读/无 git commit exit 2）
.venv/bin/python -m prodline.stages.source --root . --out artifacts/source-evidence.json

# 直接打印 JSON
.venv/bin/python -m prodline.stages.source --root .
```

`--out` 先写 `<name>.tmp` 再 `replace`，避免半个 JSON 被下游读到。

## 7. A02 消费方式

```python
import json
from pathlib import Path

from prodline.stages.source import collect_source_evidence, verify_source_evidence

root = Path(".")
evidence = collect_source_evidence(root)          # 已断言过 4 条线 + tauri + lockfiles
assert not verify_source_evidence(evidence, root)  # 落盘前再复算一次
recorded = json.loads(Path("source-evidence.json").read_text())

# manifest 的 lockfiles 必须逐字等于 source 证据（canonical 哈希）
assert recorded["lockfiles"]["uv.lock"]["sha256"].startswith("sha256:")
# product_version / upstream_tag / desktop_version 也从 versions 直接抄写，
# 不在 build 侧重新发明读取逻辑。
```

A02 的 stop condition 说“P1-source 未提供 lockfile 哈希接口 → 降级未验证”；本契约
就是那个接口：**import 名 + 字段名 + canonical 口径**都已固定。
