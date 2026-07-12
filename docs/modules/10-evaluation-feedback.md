---
title: 10 评测与反馈闭环模块
status: canonical
implementation: designed
updated: 2026-07-12
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
- 15-case Parser Golden、逐 case L0/merged-L1 baseline 和 strict CLI。
- 63 文件固定 revision 的 Parser robustness/performance manifest。
- Parser Validation GLM 工具。
- 16-case ReviewUnit v1 Golden、独立 current baseline、strict CLI 和 phase target 门禁；
  RU-1 当前为 9/9。
- 15-case FileAnalysis、14-case ChangeSet、16-case ReviewUnit v2 和 16-case ContextPlan 独立
  Golden；RU-2～RU-5 门禁已完成。
- 16-case Feature Routing Golden；正式 `FeatureRouter` 的 strict baseline 与 require-perfect
  均为 16/16。
- 4 个固定 revision 的代码语料来源：`arkui-ace-engine`、`xts-acts`、
  `applications-app-samples`、`codelabs`。
- 11 个知识来源和 4 个分析工具的来源登记，可用于后续分层评测和结果追溯。

缺失：

- Retrieval Golden Set。
- Rule precision 数据。
- Final Finding 人工标注。
- 统一运行记录、评测数据库和质量门禁。

## 3. 分层评测

| 层 | Golden 输入输出 | 核心指标 |
|---|---|---|
| Input | diff -> ChangeSet | ChangeAtom 覆盖率、行号和变更类型准确率、base/head fidelity |
| Parser | source -> Facts/Declarations | precision/recall、degraded rate |
| ReviewUnit owner | ChangeSet+Facts -> Primary Units | owner precision/recall、changed-line coverage、诊断传播、确定性 |
| Context Planner | Primary+relations+budget -> ContextPlanResult | required-context recall、relation precision/recall、distractor rejection、预算合规 |
| Feature Routing | UnitFactScope -> Tags/Dimensions/Questions | exact/routing Tag precision/recall、Unit/MR Dimension precision/recall、串扰率、问题绑定覆盖 |
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
├── review_unit/              # 现有 v1 兼容回归
├── change_set/               # RU-4 新增
├── review_unit_v2/           # RU-4 新增
├── context_plan/             # RU-5
├── feature_routing/          # Feature Routing v1
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

## 5. ReviewUnit 分阶段 Golden

ReviewUnit 的准确性不能只看“切出的代码读起来是否合理”，而要分别测量直接 owner、diff
语义和关联上下文。各阶段使用独立真值，避免一个聚合分数掩盖错误来源。

| 阶段 | Golden 与门禁 | 核心指标 |
|---|---|---|
| RU-2 | 保留现有 16-case ReviewUnit v1；目标 `14/16` | owner precision/recall、changed-line coverage、Parser diagnostics recall、输入乱序确定性 |
| RU-3 | FileAnalysis/FactOccurrence 与 CountingParser 测试 | 每个 source revision Parser 调用次数 `= 1`、exact fact provenance 完整率 |
| RU-4 | 新建 ChangeSet Golden 和 ReviewUnit v2 Golden | supported ChangeAtom coverage、changed-line coverage、base/head source fidelity 均为 `100%` |
| RU-5 | 16-case ContextPlan Golden + Planner 已完成 | Primary multiset coverage、feasible-required recall、used-edge precision/recall、distractor rejection、dispatchable budget compliance 均为 `100%` |

现有 v1 expected 是人工目标真值，后续阶段不得用 current baseline 或 Parser output 覆盖它。
RU-4 之所以另建 v2 Golden，是因为 base/head、deletion-only、rename 和精确 ChangeAtom 改变
了输入契约；这些语义不能偷塞进只表达新文件 hunk 的 v1 manifest。

RU-5 的 16 个自包含 case 覆盖单/多 Primary、直接 helper/caller 类型、同名干扰项、强弱关系
分组、无关 Primary 隔离、低中高预算、超大 Primary、base/head、degraded relation、safe owner
boundary、多问题和 multi-bundle。每个 case 至少冻结：

```text
全部 Primary
Primary 与 ReviewQuestion 的绑定
候选及其关系依据
预算下 selected / omitted Supporting
ChangeGroup / Bundle 划分
token 使用、降级原因和 diagnostics
确定性输出顺序
```

`ContextPlanResult` 通过 RU-5 门禁即表示 ReviewUnit 模块完成。Retrieval、Rules、Prompt、模型
结果和 Finding 的准确率属于后续 Golden，不纳入 ReviewUnit 完成分数。

## 6. Feature Routing Golden

`tests/golden/feature_routing/` 使用 16 个自包含、hash-pinned case，人工 expected 与
`baselines/current.json` 分离。它覆盖全部 24 Tags 和 12 Dimensions，以及：

```text
unit_exact -> exact_tags
file_hints -> routing_tags
review/retrieval/routing Dimension 与 MR conservative union
TagMatch activation signal 与配置版本
Active Review Question 与 Primary binding
同文件/跨文件串扰
fallback 与空 exact facts
timer cleanup、subscription、interaction 易混淆反例
输入排列稳定性
```

第一轮 FR-0 baseline 真实保留了 `clearInterval`、任意 `*.on` 和任意 `on*` attribute 三类差异，
没有回写 expected。迁移到 `tags-v1/dimensions-v1` 后，正式引擎当前 16/16；exact/routing Tag、
Unit/MR Dimension precision/recall、case exact accuracy 和 input-order stability 均为 `1.0`。

该 Golden 不证明全量 ArkTS taxonomy 为 100%。它已比较 QuestionBinding、配置 fingerprint 和
正式 activation trace；Active/Draft/Deprecated、完整 Dimension policy 矩阵、wheel defaults 和
结果 replay 还由模型/配置测试与 package gate 独立 fail-closed。后续增加真实场景时应扩大
Golden 分母，而不是只更新 baseline。

## 7. Retrieval Golden Set

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

## 8. Final Review Golden Set

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

## 9. 人工 adjudication

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

## 10. 反馈不能直接污染知识库

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

## 11. 运行记录

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

## 12. 质量门禁

示例门禁，具体阈值由真实数据确定：

```text
真实 Parser 样本必须存在，不能全部 skip
Parser Golden baseline 必须逐 case 完整匹配
strict L1 必须全部为 L1，不能以 optional pytest skip 代替
ReviewUnit Golden harness、schema 和 strict current baseline 测试全通过
RU-2 phase target 保持 14/16；冻结的 v1 deletion/budget 红灯不得被后续 expected 反向覆盖
RU-3 每个 source revision 严格只调用一次 Parser
RU-4 supported ChangeAtom、changed line 和 base/head fidelity 均为 100%
RU-5 Primary coverage 为 100%，所有可调度 Bundle 不超过 code context budget
RU-5 required-context、relation 和 distractor 指标达到人工 Golden 门禁
Feature Routing Golden 16/16；正式引擎 require-perfect 与 strict baseline 均通过
Feature exact Tag 不得由 file hint 或 sibling Unit 泄漏；结果必须能从 UnitFactScopes 重放
Feature QuestionBinding 只由 Active exact Tags/always_bind 产生；hint-only 不绑定专项问题
高严重级 Rules 不允许已知误报
Retrieval Recall@5 达到基线
引用合法率 100%
JSON valid rate 达到基线
```

没有真实样本时不能用“0 crash”作为通过结论。

## 13. 线上指标

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

## 14. 实验方法

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

## 15. 配置

`config/evaluation.yaml`：

```text
启用指标
Golden Set 路径
门禁阈值
样本分层
随机种子
报告输出位置
```

## 16. 技术栈

```text
pytest
JSONL/Parquet（离线样本，选型待实现）
PostgreSQL（运行和审核记录）
Jupyter/分析脚本（离线诊断）
CI 质量门禁
```

## 17. 下一步

1. RU-2 已达到现有 ReviewUnit v1 Golden 的 `14/16` phase target，并保留两个后续阶段红灯。
2. RU-3 已建立 Parser v2 FactOccurrence 和 parse-once 独立门禁，Parser v1 Golden 无漂移。
3. RU-4 已建立 ChangeSet Golden 与 ReviewUnit v2 Golden。
4. RU-5 已建立 16-case Context Golden，Planner 已通过 require-perfect、strict baseline 和预算门禁。
5. Feature Routing 已建立 16-case Golden、版本化配置、可重放 profile/result 和问题绑定。
6. 下一阶段分别建立 relation discovery、Retrieval、Rules 和 Final Review Golden；不得反向
   改变 `ContextPlanResult` 或 `FeatureRoutingResult` truth。
