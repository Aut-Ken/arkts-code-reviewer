# VideoPlayer Static / DeepSeek / Grok Tag Pilot

> 固定的真实应用 VideoPlayer base 与仓库人工构造的 head/Diff，离线重建 ChangeSet → Parser → ReviewUnit → UnitFactScope → Feature Routing → AI Tag Campaign，并整理 2026-07-21 已批准的真实 Provider 观察。

## 1. 汇报结论

- Pilot 执行状态：**PARTIAL**。
- DeepSeek 结构执行：**PASS，15/15 valid_shape**。
- Grok 裁判执行：**CLI EXECUTION ERROR，0/15 有效判断**。
- Tag 真实质量：**NOT QUALIFIED**。
- 静态 `unit_exact`：9 次分配、3 种 Tag、8/15 Unit 为空。
- DeepSeek：49 次候选 positive、10 种 Tag、3/15 Unit 为空。
- 静态的 9 次 exact 全部被 DeepSeek 覆盖；DeepSeek 另给出 40 次候选 positive。这里的“候选”不等于“正确补漏”。
- 本样本不是真实 MR，没有人工 Tag Truth，不能计算真实 Precision/Recall。
- 本次没有执行知识检索、文档质量评估、Final LLM 或 Finding。

## 2. 输入与来源

- 原仓库：`applications_app_samples`。
- 固定 revision：`8255a2987f70317cc3a2a4d46044c6b55f092bb3`。
- base 为固定真实应用源码；head 与 Diff 是仓库人工构造。
- ReviewUnit：15 个；base/head Parser 均为干净 L1。
- Provider 运行日期：2026-07-21。
- Pilot ID：`sha256:28965ae3c4ec76e50aa44d26712bb7d1acbd673f4514f6d66497e98db504f067`。
- DeepSeek Campaign：`ai-tag-shadow-campaign:sha256:85e98a9fb77d6c7b24a038616bbfe6122c13edb9bf8c7d6c8a0d328076a11b45`。
- 输入文件：[base.ets](inputs/base.ets) · [head.ets](inputs/head.ets) · [diff.patch](inputs/diff.patch) · [来源](inputs/provenance.json) · [变更说明](inputs/mutation_spec.json)。
- 脱敏 Provider 观察：[DeepSeek](inputs/deepseek_observations.json) · [Grok](inputs/grok_observations.json) · [原始文件哈希清单](inputs/live_provenance.json)。

## 3. 实际链路

```text
base/head + Diff
  → ChangeSet
  → Parser L1
  → 15 ReviewUnits
  → UnitFactScope (unit_exact / whole-file file_hints)
  → Static Feature Routing
  → 15 个完整 24-Tag DeepSeek 判断
  → 15 次 Grok blind proxy CLI 调用（均在产生判断前失败）
  → Static / DeepSeek 对照
```

这条 Pilot 在 Tag 对照处停止。它没有进入 Retrieval，也没有生成评审意见。

## 4. 静态与 DeepSeek 总体对照

| 指标 | Static unit_exact | DeepSeek candidate |
|---|---:|---:|
| Tag 分配数 | 9 | 49 |
| 不同 Tag 数 | 3 | 10 |
| 空 Unit 数 | 8 | 3 |
| 双方重合分配 | 9 | 9 |

`routing_tags` 在本样本中是同一个整文件提示签名：11 种 Tag × 15 Unit = 165 次分配。它们不是 Unit positive，不能加入上表的静态 exact 数量。

### Tag 分布

| Tag | Static exact | DeepSeek candidate positive |
|---|---:|---:|
| `has_async` | 2 | 5 |
| `has_file_io` | 0 | 1 |
| `has_interactive_component` | 2 | 2 |
| `has_lifecycle` | 0 | 3 |
| `has_logging` | 0 | 9 |
| `has_media` | 0 | 7 |
| `has_network` | 0 | 4 |
| `has_state_management` | 0 | 5 |
| `has_subscription` | 0 | 6 |
| `has_timer` | 5 | 7 |

## 5. 逐 ReviewUnit 对照

| side | ReviewUnit | Static exact | DeepSeek positive | DS-only 候选 | Grok |
|---|---|---|---|---|---|
| head | `Index.isDisposed` | — | — | — | `provider_error` |
| head | `Index.networkRetryDelayMs` | — | — | — | `provider_error` |
| head | `Index.networkRetryTimer` | — | — | — | `provider_error` |
| base | `Index.aboutToAppear` | `has_async` | `has_async`, `has_lifecycle`, `has_logging`, `has_media`, `has_state_management`, `has_subscription` | `has_lifecycle`, `has_logging`, `has_media`, `has_state_management`, `has_subscription` | `provider_error` |
| head | `Index.aboutToAppear` | `has_async` | `has_async`, `has_file_io`, `has_lifecycle`, `has_logging`, `has_media`, `has_network`, `has_state_management`, `has_subscription`, `has_timer` | `has_file_io`, `has_lifecycle`, `has_logging`, `has_media`, `has_network`, `has_state_management`, `has_subscription`, `has_timer` | `provider_error` |
| head | `Index.aboutToDisappear` | — | `has_lifecycle`, `has_logging`, `has_media`, `has_subscription`, `has_timer` | `has_lifecycle`, `has_logging`, `has_media`, `has_subscription`, `has_timer` | `provider_error` |
| base | `Index.addNetworkListener` | — | `has_async`, `has_logging`, `has_network`, `has_state_management`, `has_subscription` | `has_async`, `has_logging`, `has_network`, `has_state_management`, `has_subscription` | `provider_error` |
| head | `Index.addNetworkListener` | — | `has_async`, `has_logging`, `has_network`, `has_subscription` | `has_async`, `has_logging`, `has_network`, `has_subscription` | `provider_error` |
| head | `Index.clearNetworkRetryTimer` | `has_timer` | `has_timer` | — | `provider_error` |
| head | `Index.clearRuntimeTimers` | `has_timer` | `has_timer` | — | `provider_error` |
| head | `Index.removePlayerListeners` | — | `has_media`, `has_subscription` | `has_media`, `has_subscription` | `provider_error` |
| head | `Index.scheduleNetworkRecovery` | `has_timer` | `has_async`, `has_logging`, `has_network`, `has_timer` | `has_async`, `has_logging`, `has_network` | `provider_error` |
| head | `Index.updatePlaybackProgress` | — | `has_logging`, `has_media` | `has_logging`, `has_media` | `provider_error` |
| base | `Index.build.Column.Flex.Row.Flex.Flex.Slider` | `has_interactive_component`, `has_timer` | `has_interactive_component`, `has_logging`, `has_media`, `has_state_management`, `has_timer` | `has_logging`, `has_media`, `has_state_management` | `provider_error` |
| head | `Index.build.Column.Flex.Row.Flex.Flex.Slider` | `has_interactive_component`, `has_timer` | `has_interactive_component`, `has_logging`, `has_media`, `has_state_management`, `has_timer` | `has_logging`, `has_media`, `has_state_management` | `provider_error` |

Grok 列的 `provider_error` 表示没有模型判断，不能解释成 Grok 认为该 Unit 没有 Tag。

## 6. DeepSeek 运行证据

- 有效 Unit：`15/15`。
- 24-Tag 判断总数：`360`。
- positive / not_supported / abstain：`49` / `311` / `0`。
- 输入 / 输出 / cache-read tokens：`115022` / `19008` / `9600`。
- 单 Unit 延迟范围：`9328`～`12547` ms。
- 每个 Unit 只尝试一次，不重试。
- `valid_shape` 只证明输出满足合同，不证明判断正确。

## 7. 已观察到的过度推断风险

这次真实运行没有人工 Truth，但模型理由已经暴露出需要重点复核的候选：

1. `has_file_io`：根据 `readLRCFile` helper 名称推断文件 I/O，当前 Unit 没有直接出现 `fileIo.` 或 `fs.`。
2. `has_network`：根据 `addNetworkListener` helper 调用推断网络行为，属于 间接语义。
3. `has_timer`：根据 `clearRuntimeTimers` helper 调用推断计时器行为，属于 间接语义。
4. `has_subscription`：将 AVPlayer 的 `.on/.off` 解释成当前只为 emitter/sensor 定义的订阅 Tag。
5. `has_logging`：将自定义 `Log.info` 解释成当前配置定义的 `hilog.`。

这些只是合同对照下的风险观察，不是正式人工裁决；它们说明 DeepSeek 可以 扩大召回候选，但不能直接把 AI positive 提升为 `unit_exact`。

## 8. Grok 运行结果

- 按批准范围执行 15 次，每个 Unit 一次，无重试。
- 15 次均为 `grok_exit_1`，合计约 535 ms。
- stdout 均为空；stderr 只保留 259-byte 正文的 SHA-256，没有保留明文。
- 没有创建可验证的 Tag judgment，无法进行三方一致率或代理 P/R 对照。
- 当前证据只能证明 Grok CLI 子进程快速退出，不能证明请求已经到达服务端。
- 准确失败原因仍是 unknown；报告不把“可能的 Schema/CLI 问题”写成事实。

## 9. 机器产物

| 阶段 | 文件 |
|---|---|
| 00_run_manifest | [artifacts/00_run_manifest.json](artifacts/00_run_manifest.json) |
| 01_change_set | [artifacts/01_change_set.json](artifacts/01_change_set.json) |
| 02_parser_base | [artifacts/02_parser_base.json](artifacts/02_parser_base.json) |
| 03_parser_head | [artifacts/03_parser_head.json](artifacts/03_parser_head.json) |
| 04_review_unit_build | [artifacts/04_review_unit_build.json](artifacts/04_review_unit_build.json) |
| 05_unit_fact_scopes | [artifacts/05_unit_fact_scopes.json](artifacts/05_unit_fact_scopes.json) |
| 06_static_feature_routing | [artifacts/06_static_feature_routing.json](artifacts/06_static_feature_routing.json) |
| 07_context_plan | [artifacts/07_context_plan.json](artifacts/07_context_plan.json) |
| 08_campaign_inspection | [artifacts/08_campaign_inspection.json](artifacts/08_campaign_inspection.json) |
| 09_deepseek_results | [artifacts/09_deepseek_results.json](artifacts/09_deepseek_results.json) |
| 10_grok_results | [artifacts/10_grok_results.json](artifacts/10_grok_results.json) |
| 11_static_deepseek_comparison | [artifacts/11_static_deepseek_comparison.json](artifacts/11_static_deepseek_comparison.json) |
| 12_live_provenance | [artifacts/12_live_provenance.json](artifacts/12_live_provenance.json) |
| 13_assertions | [artifacts/13_assertions.json](artifacts/13_assertions.json) |
| 14_summary | [artifacts/14_summary.json](artifacts/14_summary.json) |

## 10. 断言

离线重建与结果整理共通过 `10` 项断言；完整证据见 [13_assertions.json](artifacts/13_assertions.json)。断言 PASS 证明的是身份、结构和统计可重放，不代表 Tag 真实质量 PASS。

## 11. 如何离线重建

```bash
cd /home/autken/Code/arkts-code-reviewer
PYTHONPATH=src .venv/bin/python E2E_test_example_2_ai_tag_pilot/run_e2e.py
```

命令只读取仓库内固定输入并重建报告，不读取 `.env`，也不会调用 DeepSeek 或 Grok。`prepare_observations.py` 仅用于从原始 `.codex` 证据重新生成脱敏 inputs，正常查看或重建报告不需要运行它。

## 12. 准确结论边界

本 Pilot 已证明：静态 exact 在该样本上的输出很少；DeepSeek 能给出更多候选 Tag；所有 DeepSeek 输出满足冻结结构；运行身份和用量可以复核。

本 Pilot 没有证明：40 个新增候选都正确、真实 Tag Precision/Recall、Grok 裁判质量、知识检索提升、相关文档质量、最终模型答案质量或生产可用性。
