# VideoPlayer Static / DeepSeek / Grok Tag Pilot

> 固定的真实应用 VideoPlayer base 与仓库人工构造的 head/Diff，离线重建 ChangeSet → Parser → ReviewUnit → UnitFactScope → Feature Routing → AI Tag Campaign，并整理 2026-07-21 已批准的真实 Provider 观察。

## 1. 汇报结论

- Pilot 执行状态：**PARTIAL**。
- DeepSeek 结构执行：**PASS，15/15 valid_shape**。
- Grok 盲判执行：**PASS，15/15 valid_shape**。
- Tag 真实质量：**NOT QUALIFIED**。
- 静态 `unit_exact`：9 次分配、3 种 Tag、8/15 Unit 为空。
- DeepSeek：49 次候选 positive、10 种 Tag、3/15 Unit 为空。
- Grok：42 次代理 positive、8 种 Tag、3/15 Unit 为空。
- 静态的 9 次 exact 全部被 DeepSeek 覆盖；DeepSeek 另给出 40 次候选 positive。这里的“候选”不等于“正确补漏”。
- DeepSeek 与 Grok 重合 41 次 positive；DeepSeek-only 8 次，Grok-only 1 次；9/15 Unit 的 positive 集合完全一致。模型一致不等于判断正确。
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
  → 15 个完整 24-Tag Grok blind proxy 判断
  → Static / DeepSeek / Grok 对照
```

这条 Pilot 在 Tag 对照处停止。它没有进入 Retrieval，也没有生成评审意见。

## 4. 静态、DeepSeek 与 Grok 总体对照

| 指标 | Static unit_exact | DeepSeek candidate | Grok proxy |
|---|---:|---:|---:|
| Tag 分配数 | 9 | 49 | 42 |
| 不同 Tag 数 | 3 | 10 | 8 |
| 空 Unit 数 | 8 | 3 | 3 |

`routing_tags` 在本样本中是同一个整文件提示签名：11 种 Tag × 15 Unit = 165 次分配。它们不是 Unit positive，不能加入上表的静态 exact 数量。

### Tag 分布

| Tag | Static exact | DeepSeek candidate positive | Grok proxy positive |
|---|---:|---:|---:|
| `has_async` | 2 | 5 | 5 |
| `has_file_io` | 0 | 1 | 0 |
| `has_interactive_component` | 2 | 2 | 2 |
| `has_lifecycle` | 0 | 3 | 3 |
| `has_logging` | 0 | 9 | 9 |
| `has_media` | 0 | 7 | 7 |
| `has_network` | 0 | 4 | 4 |
| `has_state_management` | 0 | 5 | 0 |
| `has_subscription` | 0 | 6 | 6 |
| `has_timer` | 5 | 7 | 6 |

## 5. 逐 ReviewUnit 对照

| side | ReviewUnit | Static exact | DeepSeek positive | Grok positive | 模型分歧 |
|---|---|---|---|---|---|
| head | `Index.isDisposed` | — | — | — | 一致 |
| head | `Index.networkRetryDelayMs` | — | — | — | 一致 |
| head | `Index.networkRetryTimer` | — | — | — | 一致 |
| base | `Index.aboutToAppear` | `has_async` | `has_async`, `has_lifecycle`, `has_logging`, `has_media`, `has_state_management`, `has_subscription` | `has_async`, `has_lifecycle`, `has_logging`, `has_media`, `has_subscription` | DS-only: `has_state_management` |
| head | `Index.aboutToAppear` | `has_async` | `has_async`, `has_file_io`, `has_lifecycle`, `has_logging`, `has_media`, `has_network`, `has_state_management`, `has_subscription`, `has_timer` | `has_async`, `has_lifecycle`, `has_logging`, `has_media`, `has_subscription` | DS-only: `has_file_io`, `has_network`, `has_state_management`, `has_timer` |
| head | `Index.aboutToDisappear` | — | `has_lifecycle`, `has_logging`, `has_media`, `has_subscription`, `has_timer` | `has_lifecycle`, `has_logging`, `has_media`, `has_network`, `has_subscription`, `has_timer` | Grok-only: `has_network` |
| base | `Index.addNetworkListener` | — | `has_async`, `has_logging`, `has_network`, `has_state_management`, `has_subscription` | `has_async`, `has_logging`, `has_network`, `has_subscription` | DS-only: `has_state_management` |
| head | `Index.addNetworkListener` | — | `has_async`, `has_logging`, `has_network`, `has_subscription` | `has_async`, `has_logging`, `has_network`, `has_subscription` | 一致 |
| head | `Index.clearNetworkRetryTimer` | `has_timer` | `has_timer` | `has_timer` | 一致 |
| head | `Index.clearRuntimeTimers` | `has_timer` | `has_timer` | `has_timer` | 一致 |
| head | `Index.removePlayerListeners` | — | `has_media`, `has_subscription` | `has_media`, `has_subscription` | 一致 |
| head | `Index.scheduleNetworkRecovery` | `has_timer` | `has_async`, `has_logging`, `has_network`, `has_timer` | `has_async`, `has_logging`, `has_network`, `has_timer` | 一致 |
| head | `Index.updatePlaybackProgress` | — | `has_logging`, `has_media` | `has_logging`, `has_media` | 一致 |
| base | `Index.build.Column.Flex.Row.Flex.Flex.Slider` | `has_interactive_component`, `has_timer` | `has_interactive_component`, `has_logging`, `has_media`, `has_state_management`, `has_timer` | `has_interactive_component`, `has_logging`, `has_media`, `has_timer` | DS-only: `has_state_management` |
| head | `Index.build.Column.Flex.Row.Flex.Flex.Slider` | `has_interactive_component`, `has_timer` | `has_interactive_component`, `has_logging`, `has_media`, `has_state_management`, `has_timer` | `has_interactive_component`, `has_logging`, `has_media`, `has_timer` | DS-only: `has_state_management` |

Grok 是独立盲判代理：它没有读取 DeepSeek 的结论。分歧被原样保留，没有在线裁决。

## 6. DeepSeek 运行证据

- 有效 Unit：`15/15`。
- 24-Tag 判断总数：`360`。
- positive / not_supported / abstain：`49` / `311` / `0`。
- 输入 / 输出 / cache-read tokens：`115022` / `19008` / `9600`。
- 单 Unit 延迟范围：`9328`～`12547` ms。
- 每个 Unit 只尝试一次，不重试。
- `valid_shape` 只证明输出满足合同，不证明判断正确。

## 7. DeepSeek / Grok 一致性

- 360 个二元 positive/not-positive 判断中有 `41` 个共同 positive、`310` 个共同 not-positive。
- DeepSeek-only：`8`；Grok-only：`1`。
- positive Jaccard：`0.820`；raw binary agreement：`0.975`；Cohen kappa：`0.887`。
- raw agreement 会被大量共同 negative 抬高；这些指标只描述两个模型的一致性，不是 Precision、Recall 或准确率。

## 8. 已观察到的过度推断与漏判风险

这次真实运行没有人工 Truth，但模型理由已经暴露出需要重点复核的候选：

1. `has_file_io`：根据 `readLRCFile` helper 名称推断文件 I/O，当前 Unit 没有直接出现 `fileIo.` 或 `fs.`。
2. `has_network`：根据 `addNetworkListener` helper 调用推断网络行为，属于 间接语义。
3. `has_timer`：根据 `clearRuntimeTimers` helper 调用推断计时器行为，属于 间接语义。
4. `has_subscription`：将 AVPlayer 的 `.on/.off` 解释成当前只为 emitter/sensor 定义的订阅 Tag。
5. `has_logging`：将自定义 `Log.info` 解释成当前配置定义的 `hilog.`。

6 个 Unit 的模型 positive 集合不一致。DeepSeek-only 主要是 `has_state_management`（5 次），另有 `has_file_io`、`has_network`、`has_timer` 各 1 次；Grok-only 是 `aboutToDisappear` 的 `has_network`。
这些只是合同对照下的风险观察，不是正式人工裁决。双方共同判断也可能共同偏离 Tag 合同，因此不能把一致结果直接提升为 `unit_exact` 或 Tag Truth。

## 9. Grok 运行结果

- 按批准范围执行 15 次，每个 Unit 一次，无重试。
- 有效 Unit：`15/15`；24-Tag 判断总数：`360`。
- positive / not_supported / abstain：`42` / `318` / `0`。
- 输入 / 输出 / reasoning / cache-read tokens：`369796` / `46576` / `24161` / `1920`。
- 单 Unit 延迟范围：`26214`～`73046` ms；Campaign 合计约 `613469` ms。
- `valid_shape` 证明输出完整且通过本地语义校验，不证明 Grok 是正确裁判。

## 10. 机器产物

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

## 11. 断言

离线重建与结果整理共通过 `11` 项断言；完整证据见 [13_assertions.json](artifacts/13_assertions.json)。断言 PASS 证明的是身份、结构和统计可重放，不代表 Tag 真实质量 PASS。

## 12. 如何离线重建

```bash
cd /home/autken/Code/arkts-code-reviewer
PYTHONPATH=src .venv/bin/python E2E_test_example_2_ai_tag_pilot/run_e2e.py
```

命令只读取仓库内固定输入并重建报告，不读取 `.env`，也不会调用 DeepSeek 或 Grok。`prepare_observations.py` 仅用于从原始 `.codex` 证据重新生成脱敏 inputs，正常查看或重建报告不需要运行它。

## 13. 准确结论边界

本 Pilot 已证明：静态 exact 在该样本上的输出很少；DeepSeek 能给出更多候选 Tag；DeepSeek 与 Grok 都完成 15×24 结构化判断；双方一致性、分歧、运行身份和用量可以复核。

本 Pilot 没有证明：AI 候选都正确、模型一致即真实、真实 Tag Precision/Recall、Grok 裁判质量、知识检索提升、相关文档质量、最终模型答案质量或生产可用性。
