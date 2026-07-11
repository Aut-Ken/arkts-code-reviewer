---
title: 10 评测与反馈闭环模块
status: canonical
implementation: designed
updated: 2026-07-11
---

# 10 评测与反馈闭环模块

## 1. 模块职责

为每一层建立独立可复现的质量指标，将人工接受/拒绝和 bad case 归因到正确模块。

```text
评审运行结果
-> 人工 adjudication
-> 原因归类
-> Golden Set / Bad Case
-> 对应模块修正
-> 回归验证
```

## 2. 当前状态

已有：

- Parser 和 CodeAnalyzer 单元测试。
- 12-case Parser Golden、逐 case L0/merged-L1 baseline 和 strict CLI。
- 63 文件固定 revision 的 Parser robustness/performance manifest。
- Parser Validation GLM 工具。
- 4 个固定 revision 的代码语料来源：`arkui-ace-engine`、`xts-acts`、
  `applications-app-samples`、`codelabs`。
- 11 个知识来源和 4 个分析工具的来源登记，可用于后续分层评测和结果追溯。

缺失：

- ReviewUnit Golden Set。
- Retrieval Golden Set。
- Rule precision 数据。
- Final Finding 人工标注。
- 统一运行记录、评测数据库和质量门禁。

## 3. 分层评测

| 层 | Golden 输入输出 | 核心指标 |
|---|---|---|
| Input | diff -> ChangeSet | 行号和变更类型准确率 |
| Parser | source -> Facts/Declarations | precision/recall、degraded rate |
| ReviewUnit | ChangeSet+Facts -> Units | owner/span/context 合理率 |
| Tags | Facts -> Tags | 精确集合准确率 |
| Dimensions | Tags -> Unit Dimensions | 路由准确率和串扰率 |
| Knowledge Build | docs -> Clauses | 解析覆盖、ID 稳定、来源完整 |
| Retrieval | UnitQuery -> rule_ids | Recall@K、Precision@K、MRR |
| Rules | code -> RuleFinding | precision、recall、diff relevance |
| Final LLM | ReviewRequest -> Findings | accepted rate、false positive rate |
| Output | Findings -> comments | 行号、去重、发布成功率 |

## 4. Golden Set 分类

```text
tests/golden/
├── input/
├── parser/
├── review_units/
├── tags_dimensions/
├── knowledge/
├── retrieval/
├── rules/
├── review/
└── output/
```

Golden 数据应使用开源、合成或获准内部样本，并记录来源和许可范围。

外部样本统一记录：

```jsonc
{
  "source_id": "xts-acts",
  "revision": "a616d9972cde...",
  "relative_path": ".../Sample.ets",
  "content_hash": "sha256:...",
  "sample_role": "positive | negative | boundary | real_world"
}
```

XTS 中的测试代码不能自动标记为 positive；Samples 和 Codelabs 也不能自动标记为规范
写法。`sample_role` 必须由 fixture 设计者明确说明。

## 5. Retrieval Golden Set

每条：

```jsonc
{
  "case_id": "timer-cleanup-001",
  "query": {},
  "required_rule_ids": ["RESOURCE/TIMER/R-01"],
  "acceptable_rule_ids": ["LIFECYCLE/R-02"],
  "forbidden_rule_ids": ["MEDIA/AUDIO/R-03"]
}
```

指标：

```text
Recall@5
Precision@5
MRR
empty result rate
forbidden hit rate
```

## 6. Final Review Golden Set

每条至少包含：

```text
ReviewRequest 固定输入
应报问题
不应报问题
允许 severity 范围
必须引用 rule_id
是否需要 context request
人工解释
```

LLM 非确定性测试允许语义匹配，不使用完整字符串 snapshot 作为唯一判断。

## 7. 人工 adjudication

每条 Finding 的审核状态：

```text
accepted
accepted_with_edit
rejected_false_positive
rejected_not_diff_related
rejected_duplicate
rejected_low_value
needs_more_context
```

同时记录根因：

```text
input_error
parser_error
review_unit_error
tag_dimension_error
retrieval_miss
retrieval_noise
rule_error
prompt_error
model_reasoning_error
output_mapping_error
policy_disagreement
```

## 8. 反馈不能直接污染知识库

单次 accepted Finding 不能自动成为 Baselined 规范。

知识沉淀流程：

```text
候选发现
-> Draft Clause
-> 领域 owner 审核
-> 补来源和应命中案例
-> Baselined
-> 重建索引
```

模型不得自由创建正式规则。

## 9. 运行记录

每次评审保存：

```text
ChangeSet 标识
输入/输出 hash
各模块版本
source_bundle_id 和 corpus revisions
Parser warnings
Retrieval trace
RuleFindings
LLM usage/latency
最终 Findings
发布结果
人工 adjudication
```

代码正文的保存范围和周期需经过合规评估。

## 10. 质量门禁

示例门禁，具体阈值由真实数据确定：

```text
真实 Parser 样本必须存在，不能全部 skip
Parser Golden baseline 必须逐 case 完整匹配
strict L1 必须全部为 L1，不能以 optional pytest skip 代替
ReviewUnit Golden tests 全通过
高严重级 Rules 不允许已知误报
Retrieval Recall@5 达到基线
引用合法率 100%
JSON valid rate 达到基线
```

没有真实样本时不能用“0 crash”作为通过结论。

## 11. 线上指标

```text
accepted rate
false positive rate
findings per MR
MR with zero finding rate
reference validity
retrieval empty rate
parser degraded rate
p50/p95 latency
token/cost per MR
GitCode publish success rate
```

按模型、Prompt、索引、Dimension 和代码域切分分析，不能只看总体平均值。

## 12. 实验方法

Embedding、Reranker、Prompt 和模型变更使用：

```text
离线固定 Golden Set
-> 新旧版本对比
-> 影子运行历史 MR
-> 人工抽检
-> 小范围灰度
-> 正式切换
```

每次只改变少量变量，保留消融对比。

## 13. 配置

`config/evaluation.yaml`：

```text
启用指标
Golden Set 路径
门禁阈值
样本分层
随机种子
报告输出位置
```

## 14. 技术栈

```text
pytest
JSONL/Parquet（离线样本，选型待实现）
PostgreSQL（运行和审核记录）
Jupyter/分析脚本（离线诊断）
CI 质量门禁
```

## 15. 下一步

1. 先建立 ReviewUnit 和 Retrieval 两套 Golden Set。
2. 定义统一 adjudication schema 和根因枚举。
3. 扩充 Parser Golden 的 recovery/class/generic 边界，并建立 raw-L1 diagnostics 基线。
4. 从 XTS、Samples、Codelabs 分层抽取后续可复现 fixture。
5. 端到端 tracer bullet 后开始收集 Finding accepted/rejected 数据。
