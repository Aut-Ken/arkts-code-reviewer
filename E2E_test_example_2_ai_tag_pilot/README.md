# E2E Test Example 2 — AI Tag Pilot

这是一次可复核的 VideoPlayer Static / DeepSeek / Grok Tag 对照 Pilot。目录结构与
[`E2E_test_example_1`](../E2E_test_example_1/) 对齐，但它不是第二条完整代码评审 E2E：
本样例只运行并整理到 Tag 对照，没有执行 Retrieval、Rules、Final LLM、Finding 或
GitCode 发布。

先看汇报文档：[REPORT.md](REPORT.md)。固定输入与脱敏 Provider 观察位于
[`inputs/`](inputs/)，可提交的阶段机器产物位于 [`artifacts/`](artifacts/)。

## 当前结论

- 样本：固定真实应用 VideoPlayer base + 人工构造 head/Diff，不是真实 MR。
- 静态 `unit_exact`：9 次 Tag 分配、3 种 Tag，8/15 Unit 为空。
- DeepSeek：15/15 返回合法 24-Tag 结构，49 次候选 positive、10 种 Tag。
- 静态的 9 次 exact 全部被 DeepSeek 覆盖；DeepSeek 另给出 40 次候选 positive。
- Grok：修正 plain-text Prompt 文件后缀后，15/15 返回合法 24-Tag 结构，42 次
  代理 positive、8 种 Tag。
- DeepSeek/Grok：41 次共同 positive、DeepSeek-only 8 次、Grok-only 1 次；9/15
  Unit 的 positive 集合完全一致。
- 质量状态：`not_qualified`。没有人工 Truth，不能计算真实 Precision/Recall。

“模型一致”不能写成“判断正确”。报告中已经列出若干可能的间接推断和合同越界，
例如把 helper 名称、自定义 `Log.info` 或 AVPlayer `.on/.off` 直接解释成对应 Tag。
第一次 Grok CLI 失败证据仍保存在本机原始 `.codex/.../grok/`；本目录投影并统计的是
成功的固定 `grok-rerun-1`，没有覆盖或改写第一次运行。

## 目录

```text
E2E_test_example_2_ai_tag_pilot/
├── README.md
├── REPORT.md
├── prepare_observations.py
├── run_e2e.py
├── inputs/
│   ├── base.ets
│   ├── head.ets
│   ├── diff.patch
│   ├── expected_review_units.json
│   ├── mutation_spec.json
│   ├── provenance.json
│   ├── deepseek_observations.json
│   ├── grok_observations.json
│   └── live_provenance.json
└── artifacts/
    ├── 00_run_manifest.json
    ├── 01_change_set.json
    ├── 02_parser_base.json
    ├── 03_parser_head.json
    ├── 04_review_unit_build.json
    ├── 05_unit_fact_scopes.json
    ├── 06_static_feature_routing.json
    ├── 07_context_plan.json
    ├── 08_campaign_inspection.json
    ├── 09_deepseek_results.json
    ├── 10_grok_results.json
    ├── 11_static_deepseek_comparison.json
    ├── 12_live_provenance.json
    ├── 13_assertions.json
    └── 14_summary.json
```

## 离线重建报告

```bash
cd /home/autken/Code/arkts-code-reviewer
PYTHONPATH=src .venv/bin/python E2E_test_example_2_ai_tag_pilot/run_e2e.py
```

该命令会重新执行固定的 ChangeSet → Parser → ReviewUnit → UnitFactScope → Feature
Routing → Campaign inspection，然后校验仓库内已经冻结的 Provider observation inputs，
最后原子重写 `artifacts/` 和 `REPORT.md`。

它不会：

- 读取 `.env` 或 API Key；
- 调用 DeepSeek 或 Grok；
- 发送源码或 Prompt；
- 连接数据库或执行 Retrieval。

## Provider observation inputs

`inputs/deepseek_observations.json` 和 `inputs/grok_observations.json` 是从本机原始
`.codex` 运行产物生成的脱敏、固定投影。它们保留判断、reason、usage、latency、运行
状态和源文件 SHA-256，但不包含 API Key、Authorization header、Prompt/源码请求正文、
wire body 或原始 Provider 响应正文。

`prepare_observations.py` 只用于重新验证并导入同一批原始本机证据。正常查看与离线重建
不需要运行它。它同样不会发起网络请求，也不会读取凭据。

## 准确解读

本目录证明的是：

- 上游 15 个 ReviewUnit 可以确定性重建；
- 静态 exact 在该样本中确实很少；
- DeepSeek 的完整 24-Tag 输出结构真实跑通，并给出更多候选；
- Grok 的完整 24-Tag 盲判真实跑通，且与 DeepSeek 的一致和分歧均被保留；
- 运行 identity、尝试次数、用量和结果可以在仓库内查看。

本目录没有证明：

- DeepSeek 的 49 个 positive 全部正确；
- 新增 40 个候选就是 40 个真实漏标；
- Grok 与 DeepSeek 一致就代表 Tag Truth；
- Grok 已经被证明是可靠裁判；
- Tag、文档检索或最终评审答案达到生产质量。
