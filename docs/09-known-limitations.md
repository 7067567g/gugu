# 09 · P0 已知限制（0.9.0）

> 这份清单是 P0 收口的一部分：**没有修完的，不藏**。每条都写了影响、证据和去向
> （`runs/gugu-p0-m3-dogfood/reports/NEXT-BACKLOG.md` 的编号）。
> P0 范围与非目标见 `strategy/product/gugu-p0-consolidation.md`（外部工作区）。

## 1. owner 真实 dogfood 未完成（B-01）

P0 §8 第 8 条要求 owner 用一次真实任务。M3 由一个 headless 进程执行，只能跑自动化
路径（`hero_flow` + `gugu_capability`），不能代替 owner 在真实桌面里的点击、澄清与判断。
现有证据是：4 元素 hero flow、原地修正、导出、11 能力枚举、guidance 28 条零残留
vitest；**不是**“owner 已验收”。
→ 发布前由 owner 按 `docs/08-p0-usage.md` §4 走一遍并记录耗时/干预/问题。

## 2. 真实模型没有实测（B-02，M3b 仍 BLOCKED）

M3 环境无网关凭据，3 张配图与修正图全部是显式 mock（`mock=true`、`MOCK-*`、水印）。
M3b 再次探测：`gateway_credentials()` 为空、`GUGU_IMAGE_*`/`OPENAI_*` 未设置、
`~/.hermes/config.yaml` 不存在，因此真实 `qwen-image-2.0` 生成 **BLOCKED**，没有把
本地 HTTP stub 当成厂商实测（证据：`runs/gugu-p0-m3b-global-reach/reports/G3-BLOCKED.md`）。
主/备/mock 阶梯只由单测与失败注入证明；“真实 qwen/gpt 网关可用、真实延迟、失败率”
仍未验证。0.006s 的 time-to-first-artifact 不能当真实创作耗时。

## 3. `navigate` 已真执行，但仍走邮箱轮询（B-03/B-04）

M3b 起 `navigate(screen)` 是 `ready (M3)`：写 `designs/navigation.json`，app 级 runtime
约 250ms 轮询后调 `host.navigate()`，再用 `agent-state.json` 的 `nav_request_id` 确认。
因此：
- 桌面渲染端没运行时返回 `navigated=false`（`no_renderer`/`timeout`），不是假成功；
- 仍不是 renderer-directed host RPC/WS，跨进程延迟最坏约 250ms + 一次发布；
- 远端 backend 拓扑下，工具写的是 backend 的 `HERMES_HOME/designs`，不是桌面客户端的
  文件（与 B-09 同一个范围问题）。

## 4. 引导已全局，但仍不是实时（B-04）

M3b 把消费循环从 `/design` 提升到 app 级 runtime（任意界面可 highlight/box/label）。
限制变成：
- Agent 调用到屏幕可见最多约 1.2s（轮询，不是推送）；
- 锚点必须在**当前** surface；跨界面要先 `navigate` 再 `highlight`，否则该步被 dismiss；
- 多步引导 / 滚动跟随未实现（overlay 坐标在 show 时算一次）。
零残留契约由 28 条 manager vitest + 13 条 app 级 runtime vitest 覆盖（7 个中断源 +
超时 + stop 后 overlay/target/listener/timer 全零）。

## 5. `human_interventions` 是常量（B-05）

`plugins/gugu_capabilities/hero_flow.py` 的 summary 里 `human_interventions: 0` 是写死的
字段，不是现场计数。自动化路径确实无人干预，但它不能证明“真实使用中干预 ≤2”。
真实 dogfood 必须人工计数。

## 6. 导出 manifest 没有产品版本（B-06）

`manifest.json` 有 `format: gugu.canvas-export/v1`、元素/资产/模型/mock/逐资产 sha256，
但没有 `product_version`，产物无法自证由 0.9.0 生成。补字段要改 `export.py`，不在
M3 写白名单内。

## 7. jsdom 没有布局，真实 Electron 视觉回归未做（B-07）

桌面零残留测试在 jsdom 里断言 DOM/listener/timer/焦点/滚动/布局结构；“overlay 在真实
窗口的对比度、遮挡、DPI、父级 transform 行为”没有像素级证据。发布前建议在有显示环境的
机器上截图确认。

## 8. Python↔TS 锚点仍人工同步（B-08）

`registry.ANCHORS` 与 TSX 的 `data-agent-id` 是两份人工维护的清单；两侧各有测试，但没有
共享生成器/漂移检测。新增锚点时漏改一侧不会被现有门禁抓住。

## 9. 远端 backend 语义（B-09）

`gugu_capability` 随 bundled 插件常驻，读写的是**当前 backend 的**
`HERMES_HOME/designs`，不是桌面客户端的文件。工具没有按 session source 收口到
`desktop_ui` 一类的 session-scoped toolset；在远端拓扑上不要把它当“操作桌面画布”。

## 10. 画布写入不触发 dashboard 实时广播（B-10）

Agent 直写 `project.json` 不会触发 `/events`；渲染端在下次保存（409 → 重拉）或重新进入
工程时才看到改动。dashboard 保存也不参与 `agent.lock`，靠 revision 乐观锁兜底。

## 11. P0 明确不做（不是缺陷，是范围）

Windows 产线、Code 深度、视频/故事板、协作/多用户、插件市场、常驻 tour/遮罩教程/录屏、
多端、画布版本树、自动 A/B 评测。见产品计划 §10 停车场。

## 12. 版本线说明

`GUGU_VERSION=0.9.0`（产品）；上游内核 `pyproject.toml=0.21.5`、桌面包
`apps/desktop/package.json=0.17.6` 是**设计上的解耦**，不随产品版本递增。
`version-sources.json` 是四源机器清单，`tests/ci/test_gugu_release_consistency.py`
只断言产品三件套（`GUGU_VERSION` / CHANGELOG / README）与 manifest 关系。
