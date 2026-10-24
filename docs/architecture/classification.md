# 架构归类规则（K / X / G / P）

> 本文回答三件事：**代码属于哪一类**、**类之间允许怎么依赖**、**基线棘轮什么时候、由谁、按什么纪律更新**。
>
> **唯一事实源**是 [`arch/architecture.yaml`](../../arch/architecture.yaml)；本文解释它，不复制会漂移的模块清单或计数。
> 当前数字以 [`arch/baseline.json`](../../arch/baseline.json) 为准，门禁实现见
> [`scripts/arch/check_boundaries.py`](../../scripts/arch/check_boundaries.py)。
>
> 适用版本：v0.8.0 起（基线 `6737fff0`）。维护者：账本 `maintainer` 字段。

---

## 0. 一条铁律：目录不决定类

**目录名只是线索，`arch/architecture.yaml` 里的 `class` 才是判定。**

迁移期目录会撒谎：`hermes_cli/` 整包是 G，但 `config.py` / `auth*.py` /
`models*.py` / `version_source.py` 是事实上的 K 服务（被 `agent/` 引用 442 次）。
账本先按现状整包记录，P5 再把它们搬进 `kernel/` 并留 re-export shim；搬家后
基线应自然下降，届时按 §5 显式更新。

同理：

- 被 K import 的模块**不会**因此变成 K。K→G 是违规，正确处置是搬家或上收契约，
  不是把 G 改判成 K。
- `tests/`、`scripts/`、`evals/` 不是“没有类”，是 **P**。
- `docs/`、`assets/`、`locales/` 等无 Python import 面的目录是 **unclassified**
  （显式登记 + 理由），不是四类之一，也不参与违规判定。

---

## 1. 四类定义

| | **K 基础**（kernel / core services） | **X 扩展**（extensions） | **G 胶水**（glue） | **P 产线工具**（prodline） |
|---|---|---|---|---|
| **一句话判据** | 定义产品语义、持有跨请求状态 | 第三方按版本独立交付，可单独装/禁/升 | 只做翻译与承载，不自持领域规则 | 只在构建/安装/验收/取证/测试时运行，产品进程永不 import |
| **职责** | 会话、turn、压缩、工具运行时、审批、provider 路由、状态持久化、配置、认证、成本、版本读取 | 具体厂商 / 平台 / 技能 / UI 面 | 进程宿主、协议宿主、UI 壳、平台适配器、CLI 参数解析 | 构建、安装、soak、验收、回执、门禁、测试、文档站生成 |
| **允许依赖** | 仅 K | K、X | K、X、G | 任意层（**只读**） |
| **禁止依赖** | X、G、P | G、P | P | 无（但产品代码不得 import P） |
| **账本 owner** | `kernel-group` | `plugin-authors` | `glue-group` | `release-manager` |
| **当前目录（迁移期）** | `agent/ tools/ providers/ hermes_platform/ cron/` + `hermes_state*.py` 等根级内核 | `plugins/ skills/ optional-skills/ optional-mcps/` | `hermes_cli/ gateway/ tui_gateway/ acp_adapter/ apps/ web/ ui-tui/` + 根级入口 | `scripts/ tests/ evals/ website/ docker/ nix/ tests-js/` + `setup.py` 等 |
| **目标目录（P5/P6）** | 新增 `kernel/`，`contracts/` 提升为显式契约 | `plugins/<id>/` 单一 manifest + 契约版本 | `glue/{desktop,hosts,cli,tui,platforms}/` | 新增唯一 `prodline/` |

**unclassified**：当前无法按上述判据给出可信归类，或根本不是代码模块。必须写
`class: unclassified` + 非空 `reason`，且不得声明 `allowed_deps` / `forbidden_deps`。
checker 对涉及 unclassified 的边**只计数、不判违规**；但某个 `.py` 文件匹配不到任何
账本规则时，checker 直接 **exit 2**（配置错误），强制新模块先归类。

---

## 2. 依赖方向矩阵

行 = import 方，列 = 被 import 方；✅ 允许，❌ 禁止。

| import 方 ↓ / 被 import 方 → | **K** | **X** | **G** | **P** |
|---|---|---|---|---|
| **K** | ✅ | ❌ | ❌ | ❌ |
| **X** | ✅ | ✅ | ❌ | ❌ |
| **G** | ✅ | ✅ | ✅ | ❌ |
| **P** | ✅ | ✅ | ✅ | ✅ |

- 未列出的 `unclassified` 行/列：**不判定**（计 `unclassified_edges`，见 §5.4）。
- 矩阵与 checker 的 `forbidden_pairs()` 必须同源：**K→X、K→G、K→P、X→G、X→P、G→P**
  这 6 对是门禁唯一会比较的 pair。`arch/architecture.yaml` 的 `classes.*.allowed_deps`
  与 `forbidden_deps` 是矩阵的机器可读版本；每个 module 条目的这两个列表必须与所属类
  集合相等，加载时不一致直接 exit 2（防止两处规则漂移）。
- **方向解释**：
  - **X→G 禁止、G→X 允许**：G 负责承载 X，X 只能依赖 K 的契约。X 若需要 G 的内部
    实现，说明契约缺失——把公共面提升到 `contracts/`（K），而不是反向依赖。
  - **K→G 禁止**：内核被外围宿主 import 是最严重的一类倒挂；正确处置是搬家 + shim，
    不是改判类。
  - **P 全允许但只读**：P 可以 import 任何层做检查/构建，但产品进程（K/X/G）永不
    import P。`P→*` 不计违规；`*→P` 中 K→P、X→P、G→P 全部禁止。
  - **同层依赖**：K→K、X→X、G→G、P→P 允许；同层内部也要避免循环，但循环不由本门禁判定。

---

## 3. 新代码放哪：6 个判定问题

**按 Q1 → Q6 顺序问，第一个“是”就是答案。** 6 问全“否”说明代码有问题（重复了现有
能力，或需要新契约），不要靠“感觉”归一个类。

| # | 问题 | 是 → | 否 → |
|---|---|---|---|
| **Q1** | 它是否只在构建 / 安装 / 验收 / soak / 取证 / 测试 / 文档生成时运行，且**产品进程永不 import 它**？ | **P** | Q2 |
| **Q2** | 它是否由第三方独立交付，能单独安装 / 禁用 / 升级，且不随产品版本一起发？ | **X** | Q3 |
| **Q3** | 它是否**定义领域规则**或**持有跨请求状态**（会话、turn、审批、配置、认证、成本、任务、版本）？ | **K** | Q4 |
| **Q4** | 它是否只是把外部协议 / 平台 / UI / 命令行参数**翻译**成内核调用，自身不持有跨请求状态？ | **G** | Q5 |
| **Q5** | 删掉它，产品的**可观察行为**是否完全不变？ | **G**（或直接删） | Q6 |
| **Q6** | 它是否需要被壳 / 插件在**运行时动态发现**（注册表、slot、hook）？ | 拆两半：**注册表与发现协议 → K（`contracts/`）**；**实现 → X** | **K**（说明它是隐含内核，先补契约再进） |

### 3.1 判定示例（用本仓真实模块）

| 模块 | Q1 | Q2 | Q3 | Q4 | Q5 | 结果 |
|---|---|---|---|---|---|---|
| `hermes_cli/config.py` | 否 | 否 | **是**（持配置状态） | — | — | **K**（当前暂记在 G 包内，P5 搬家） |
| `hermes_cli/plugins.py` | 否 | 否 | **是**（持插件注册表） | — | — | **K**（注册表属 K，具体插件属 X） |
| `plugins/gugu-design` | 否 | **是** | — | — | — | **X** |
| `gateway/platforms/slack/` | 否 | 否 | 否 | **是** | — | **G** |
| `apps/desktop/electron/backend-ready.ts` | 否 | 否 | 否 | 否 | 否 | **G**（“就绪定义”应上收到 K 的 health 契约，壳降为薄客户端） |
| `scripts/arch/check_boundaries.py` | **是** | — | — | — | — | **P**（门禁，不是产品工具） |
| `tests/ci/test_ci_workflow_wiring.py` | **是** | — | — | — | — | **P** |
| `docs/architecture/classification.md` | 不适用（非代码） | — | — | — | — | **unclassified**（文档数据；登记理由） |

### 3.2 三个常见误判

1. **“共享工具函数”不等于 K**：先问它是否持状态 / 定语义（Q3）。纯字符串/格式化
   函数放 K 或 G 取决于调用方与语义归属；不要因为“大家都用”就塞进 K。
2. **“被 K import”不等于 K**：这是违规信号，不是分类依据。见 §0。
3. **“先标 unclassified 以后再说”不是免死金牌**：必须写理由 + 归属 owner + 复核
   里程碑（当前 7 个数据目录已有理由；新增 unclassified 要在 PR 里说明何时重判）。

---

## 4. 怎么登记一个新模块

1. 在 `arch/architecture.yaml` 的 `modules:` 里加条目。**规则按顺序匹配，first match wins**；
   精细规则放在宽规则之前。
2. 必须字段：`id`（唯一）、`paths`（glob 列表）、`class`、`owner`。
   `allowed_deps` / `forbidden_deps` 必须与所属类的默认矩阵**集合相等**（顺序不限）。
3. 若新目录包含 `.py` 却没登记，`check_boundaries.py` 会报
   `unclassified Python files/modules found` 并 **exit 2**——这是故意的，不允许新模块
   静默逃逸。
4. 登记后先本地跑 gate；若计数变化，按 §5 单独提交基线更新。

```yaml
  - id: mypkg
    paths: ["mypkg/**"]
    class: X
    owner: plugin-authors
    allowed_deps: [K, X]
    forbidden_deps: [G, P]
    note: "独立交付的扩展；只依赖 K 契约"
```

`unclassified` 示例：

```yaml
  - id: mydata
    paths: ["mydata/**"]
    class: unclassified
    owner: TBD
    reason: "运行时数据文件，无 Python import 面；P5 目录治理时重判"
```

---

## 5. 基线棘轮：门禁语义与更新纪律

### 5.1 门禁只看一件事

`current <= baseline` 才 PASS。比较分两层（实现见 `compare()`）：

- **class pair 是门**：只对 §2 的 6 个 forbidden pair 比较计数；相等 = PASS，超出 = **exit 1**。
- **module pair 是定位器**：回归时打印 `agent->hermes_cli: 443 > 442 (+1)`，指出增长
  发生在哪个模块对，但**不单独 gate**，避免把同层合法重排判红。
- **基线内旧违规不会让门变红**：1562 条是 seed 现状，不是“可接受目标”；棘轮只减不增。
- **退出码**：`0` 基线内；`1` 违规增长；`2` 配置/扫描错误（账本非法、缺 baseline、
  有 `.py` 未归类、PyYAML 缺失）。
- **规则漂移**：`architecture.yaml` 的 sha256 与 baseline 不一致时只打 WARN，计数照常比较；
  改规则后应显式重 seed（§5.3）。

### 5.2 谁可以更新

| 角色 | 权限 |
| --- | --- |
| **账本 maintainer**（`arch/architecture.yaml` 的 `maintainer`，当前 `MAIN`） | 唯一可以执行 `--update-baseline` 并提交 `arch/baseline.json` 的人 |
| **类 owner**（K=`kernel-group` / X=`plugin-authors` / G=`glue-group` / P=`release-manager`） | 提出规则/基线变更并 review；不能单方面改别人类的边 |
| **PR 作者（含业务开发者）** | 可以**提议**（在 PR 描述里给出计数与理由），**不得自行改基线让 CI 变绿** |
| **CI** | **永不**更新基线；只跑 `--baseline arch/baseline.json` 的只读检查 |

硬要求：基线变更的 PR 必须由**至少一名非作者的 arch reviewer** 批准；涉及 P 计数的
变更需 `release-manager` 复核。作者不能自我批准。

### 5.3 什么时候可以更新（以及必须附什么理由）

允许的四种情形：

1. **账本规则有意变化**：模块搬家、类改判、新增模块首次登记导致计数变化；
2. **违规真实下降**：搬迁/契约化完成后，棘轮**下调**（推荐，且必须做）；
3. **新模块首次 seed**：确实带入存量违规，且 PR 解释为什么不能本期清零；
4. **基线文件损坏/丢失后的恢复**：需引用上一个有效 commit 与原因。

命令（**必须**同时给非空 `--reason`，否则 exit 2）：

```bash
# 1. 先看现状：红了是因为 regressions 还是 improvements
python3 scripts/arch/check_boundaries.py --baseline arch/baseline.json

# 2. 显式重 seed；reason 会写进 JSON，review 时可见
python3 scripts/arch/check_boundaries.py --update-baseline \
  --reason "P5: move config/auth/models to kernel/; K->G 1052 -> 940 (-112), no new edges (issue #123)"

# 3. 审 diff：counts 只应下降，除非 reason 明确解释上升
git diff -- arch/baseline.json
```

`--reason` 至少写清四件事：**触发原因**（规则变更 / 迁移阶段 / 真实下降）、**关联
issue 或 PR**、**前后计数**、**为什么不是本期零增长**。JSON 会同时记录
`commit`、`architecture_sha256`、`generated_at`，便于审计。

**下降也要显式更新**：`scripts/arch/test_check_boundaries.py` 断言
`cmp.improvements == []`，所以计数低于基线同样会让测试红——这是为了逼棘轮及时下调，
不是让门禁自动改文件。**checker 永不自动写 baseline。**

### 5.4 明令禁止

1. 为了让 CI 变绿而**调高**基线接受新增违规；
2. 无 `--reason` / 空 reason / 只写“update baseline”这类无信息理由；
3. 手改 `arch/baseline.json`（绕过 reason/commit/sha 记录）；
4. 在 CI 里跑 `--update-baseline`，或给 `arch-gate` 加 `continue-on-error` / `|| true`；
5. 把一条真实违规的模块改判成 `unclassified` 来消计数（只有“确实无法归类”才允许，
   且必须有理由与复核里程碑）；
6. 把基线变更和业务改动混进同一个 commit（基线变更应是独立的 `chore(arch): ...`）。

---

## 6. 本地与 CI 怎么跑

### 6.1 本地

```bash
# 硬门（与 CI 完全同一条命令）
python3 scripts/arch/check_boundaries.py --baseline arch/baseline.json

# checker 自测（正/负样例，零 pytest 依赖）
python3 scripts/arch/check_boundaries.py --self-test

# checker 自测套件（含 ledger 覆盖、baseline sha 一致性、真仓 current <= baseline）
python3 -m pytest scripts/arch -q

# 机器可读回执（排查用；不参与 gate）
python3 scripts/arch/check_boundaries.py --baseline arch/baseline.json \
  --format json --json-out /tmp/arch-receipt.json
```

### 6.2 CI

`.github/workflows/ci.yml` 的 `arch-gate` job（Python 3.12）：

1. `actions/checkout@v4`；
2. `actions/setup-python@v5`；
3. 只安装 `pyyaml==6.0.3`（`pyproject.toml` 既有 pin；checker 其余全部是标准库）；
4. 跑 `python scripts/arch/check_boundaries.py --baseline arch/baseline.json`；
5. 该 job 是 `all-checks-passed` 的 `needs` 之一，失败会让聚合 required check 变红。

**CI 失败怎么读**：先看 `regressions`（哪个 class pair、超了多少），再看
`module pairs above baseline`（增长落在哪个模块对），最后看 sample 的 `file:line`。
如果是 `exit 2`，不是“违规增长”，而是账本/环境问题（未归类文件、缺 baseline、
没装 PyYAML）——按提示修配置，不要动计数。

### 6.3 改完自检清单

- [ ] `python3 scripts/arch/check_boundaries.py --baseline arch/baseline.json` → exit 0；
- [ ] `python3 scripts/arch/check_boundaries.py --self-test` → all cases passed；
- [ ] `python3 -m pytest scripts/arch -q` → 全绿；
- [ ] 新模块已在 `arch/architecture.yaml` 登记，且 `allowed_deps` / `forbidden_deps`
      与类默认一致；
- [ ] 若动了 `arch/baseline.json`：reason 已写、diff 已审、独立 commit、非作者 review；
- [ ] 没有为过门而放宽规则、改 `unclassified` 或删检查。

---

## 7. 与后续阶段的关系

- **r01（已完成）**：账本 + ast checker + 棘轮基线 + 正负自测。
- **r02（本轮）**：CI 接线 + 本文档。
- **P5**：`hermes_cli/` 里的 K 服务（config/auth/models/version_source）搬进 `kernel/`
  并留 shim；届时按 §5.3 下调基线。
- **P6**：`contracts/` 契约版本化 + `plugins/<id>/` 单一 manifest；X→G 的 484 条按
  契约逐步消化。
- 本文档不承诺“清零”1562 条存量违规；棘轮的目标是**只减不增**，每一步下降都有 commit
  与 reason 可审。
