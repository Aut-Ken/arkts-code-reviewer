---
title: 10 评测与反馈闭环模块
status: canonical
implementation: designed
updated: 2026-07-15
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

FR-02/FR-02B lifecycle 评估与 FR-0 Golden 分离。
`tag-retrieval-truth-observation-v1` 保持冻结；FR-02 v3 使用 observation-v2，FR-02B v4
必须使用 `tag-retrieval-truth-observation-v3`。后两者记录 feature-config identity、
`feature_routing_schema_version`、每 case 的 exact/routing Tag 集合、`unit_exact/file_hints`
两层 symbols 和完整 `tag_matches` trace；observation-v3 还必须保存 owner-context diagnostics 及
per-symbol owner role、symbol occurrence、direct/enclosing owner declaration 和 role evidence
occurrence IDs。这些字段不得回填到 v1 artifact；默认 checker 和正式 v1 结果不变。

`tag-config-v3 + feature-routing-v2` 的纯 leaf 结果只保留为 development regression。
FR-02B 使用 `tag-config-v4 + feature-routing-v3`：exact operator
`any_unit_symbol_leaf_with_owner_role` 按以下映射求值：

```text
aboutToAppear/aboutToDisappear -> arkui_custom_component
onBackPress/onPageHide/onPageShow -> arkui_router_page
onReady -> excluded from owner-aware exact; retained as routing-only file hint
```

Routing-only `any_file_symbol_leaf` 只在 `file_hint` 求值，不能为当前 Unit 声称 owner
role、绑定专项 Review Question 或成为 Finding evidence。Owner role 由既有 FileAnalysis
证据在 Feature Routing 边界派生，Parser schema 与 Parser v1 行为不变。E2E 必须同时覆盖
method Unit、自定义组件 struct Unit 的直接 lifecycle method 子声明，以及嵌套 ordinary
class 同名 method 的 abstain。

原 7 个 cross-target lifecycle additions 已记录人工正裁决（非 blind、非独立）；
`TR-TIMER-008` 仍是明确要求 lifecycle co-Tag 的 case contract。它们现在可以作为
development regression truth，而不是未标注预测。但当前 48 case 全部已参与 matcher、
routing 和报告合同设计；历史
`calibration/acceptance_holdout` 只是 fixture split 名称，任何一组都不能再充当独立 blind
holdout。7 个 lifecycle-target 正例、5 个 lifecycle-target 反例及上述正裁决可以证明已知
合同重放，却不能给出候选规则的总体 Precision/Recall。

Owner-aware 约束修复了纯 leaf 无法区分普通 class 同名方法的已知合同缺口，但独立 blind
holdout 仍缺失。因此 FR-02B report 必须继续给出 `activation_ready=false`；candidate
contract 通过不等于生产质量合格或默认配置已切换。

当前 pinned E2E 的 `lifecycle-owner-role-evaluation-v1` 结果是：15 个已声明/已裁决
lifecycle exact additions 与 5 个已知 negative 在 selected regression 上得到
`15 TP / 5 TN / 0 FP / 0 FN`，`declared_contract_gate.passed=true`；同时
`candidate_evidence_gate.passed=false`、`activation_ready=false`，关闭原因固定为
`truth_is_provisional`、`development_regression_only` 和
`independent_adjudicated_holdout_missing`。这组结果应成对报告，不能只展示前一个通过项。

### 6.1 Lifecycle independent blind holdout v1

FR-02B 现在有 fail-closed holdout 基础设施，但尚没有真实 holdout Truth。它不修改、替换
`tag-retrieval-truth-v2` 或 48-case development manifest，而是使用以下不可倒序的数据链：

```text
冻结 candidate commit/config/完整 runtime + evaluation harness
-> 外部独立 custodian 生成无标签 selection
-> 从 canonical contract/policy + selection/checkout 构建 blind packet
-> 两名独立人工 reviewer 的完整 receipt
-> consensus
-> Git seal commit
-> 预期首次运行 candidate
-> lifecycle-owner-role-holdout-evaluation-v1
```

核心合同位于
`retrieval_validation/lifecycle_blind_holdout.py`，post-seal evaluator 位于
`retrieval_validation/lifecycle_blind_holdout_evaluation.py`；完整操作说明见
`tests/evaluation/lifecycle_blind_holdout_v1/README.md`。

候选设计期已看过 `applications_app_samples` 在
`8255a2987f70317cc3a2a4d46044c6b55f092bb3` 的完整 tracked tree，因此该 revision 本身不能
作为独立 holdout。Selection revision 必须是它的严格后继 descendant；每个 source 相对整棵
exposure tree 都必须同时是全新 path-derived family、全新 path 和全新 Git blob/content。
此外还必须完整绑定并排除 canonical development Truth 的 family/path/content，CLI 不允许替换
这份 Truth。泄漏检查固定在 development family/path/content 三个边界。

V1 是固定的 `purposive_stratified_challenge_holdout`：恰好 32 case、32 family、每 family
最多 1 case。正例为 `component_v1_positive=4`、`component_v2_positive=4`、
`router_page_positive=8`；四类 critical negative
`nested_owner_negative/non_entry_page_negative/ordinary_owner_negative/routing_only_negative`
各 4。它不声称随机抽样、已知 inclusion probability 或 natural prevalence，并固定
`natural_prevalence_claimed=false`。当前 eligible corpus 没有独立的 non-DocsSample
`@ComponentV2` family，所以不能按设计构造真实 selection；不得删除 V2 stratum、从 exposure
revision 借样或用 development case 补齐。

Selection 的 candidate freeze 覆盖 candidate commit 的完整
`src/arkts_code_reviewer` Python tree、默认 tags/dimensions、Parser sidecar、candidate config，
以及 Python version/packages/platform、Node version/executable hash、完整 `node_modules` tree
fingerprint。Evaluation harness 也由独立 commit、固定文件集和 fingerprint 冻结。只冻结少量
手写 runtime 文件不足以重放正式结果。

Review packet 不包含 candidate identity/output、challenge stratum、selection rank 或任何
expected/actual 字段。Packet builder 没有 `--tag-contract`/`--review-policy` 参数，而是从仓库
固定路径读取 canonical 文本；formal evaluator 会从 selection、验证后的 checkout 和这两份
canonical 文本重建 packet 并要求完全相等。两份 receipt 必须由不同 human reviewer 完整覆盖
相同 32 case；label 和完整 ReviewUnit identity 均一致才形成 agreed Truth。任何 disagreement
或 `needs_taxonomy_decision` 都保留为 blocker，不能删除分歧 case 或在揭盲后用第三票修指标。

正式 CLI 必须从全新 seal checkout、仓库外非 editable virtualenv 启动，使用空 `PYTHONPATH`
及 Python `-P -B -S`。它先执行不 import `arkts_code_reviewer` 的纯标准库 preflight，验证指定
full seal revision、`HEAD == seal_revision`、完全 clean worktree、五份 artifact 的 committed
bytes、完整 `src` import closure，以及 candidate runtime/environment 和 evaluation harness；
ignored bytecode/native extension、symlink 或额外源码都会 fail closed。成功后才加入已验证的
repository `src` 与外部依赖目录并 import typed evaluator；包的 eager import 会
在此时执行已经验证过的项目源码，但不会加载 candidate config、实例化或运行 `FeatureRouter`。
Typed validation 随后把 current source bytes 与 pinned revision Git blob 强绑定，再验证 policy、
development exclusions、exposure boundary，
重建 packet 和 complete consensus；全部通过后才加载并运行候选。运行后 Parser risk、ReviewUnit/build risk、
UnitFactScope risk、owner provenance、challenge-owner evidence、file-hint promotion 和
routing-only contract failure 都必须为 0。Python 直接/传递依赖版本会冻结，但外部 virtualenv
package bytes 仍是可信 host/container 边界，不能由仓库内自证。

默认 CLI 对合法但 non-ready 的 report 返回 1；只有 `--report-only` 才允许这种报告返回 0，
且不会改变 `evidence_ready`。`--omit-cases` 只删除 case rows，并显式输出
`case_details_omitted=true` marker；随后生成绑定实际输出形状的 `evaluation_id` 自哈希。该 hash
只检测报告漂移，不认证 runner；校验/运行错误仍返回 2。

门禁通过只输出 `evidence_ready=true`，不会修改默认 `tags-v1`。报告中的
`production_activation.activation_ready` 固定为 `false`；正式迁移仍需单独产品决策、配置版本、
Golden/E2E 迁移和回滚方案。当前没有独立 selection、两份外部 receipt 或 sealed consensus，
因此没有运行真实 holdout candidate，FR-02B 仍是未激活 candidate。

Schema、hash、Git ancestry 和 hostile-runtime tests 只能证明受检仓库内的形状、内容、顺序与
fail-closed 行为。Human blinding、negative stratum category 和“首次运行”都是可审计流程声明，
不是密码学证明；它们不能证明 reviewer 身份、selector 未私下挑样、候选从未在别处运行或 host
未被控制。生产级证据还需要独立 CI/container、外部身份/权限和留存日志，不能只在候选团队工作区
内自证。

### 6.2 Generic Tag Truth v2 与 coverage registry

Lifecycle v1 已证明双审、seal 和 post-seal first-run 的治理链可实现，但其 candidate、strata、
owner role 和叶子方法均为 lifecycle 专用；RelationalStore 的 `tag-truth-v1` 已有真实 owner、
hard-negative、P/R/F1、诊断和 strict baseline，却把 case ID、Draft candidate 和
`any_import_use` 写入专用 schema，且 Truth 永远是 provisional。两者都不得原地改成“通用版”。

EVAL-01A Stage 1 新增 `tag-contract-snapshot-v1` 与 `tag-truth-v2`，统一以下 real-code evidence
粒度：

```text
repository revision + source path/blob
+ ReviewUnit kind/qualified symbol/inclusive span
+ target_tag_id
+ exact label + routing-hint label
```

每个 suite 只评一个目标 Tag；没有显式 judgement 的其他 Tag 不算 negative。Source 还冻结
path-derived family、content hash、normalized ReviewUnit body 和 template cluster；Stage 1 只保存
可审计 identity，不定义 similarity 算法或阈值。Near-duplicate check 未明确 qualified 时整个数据集
仍是 `not_qualified`，这些 hash 不能证明语义改写后的样本独立。Tag contract 只写
positive/negative/abstain 语义边界，不写 matcher operator 或 candidate 输出。Exact 与
routing-hint 是两条独立判断轴；每条轴都必须分别冻结 positive、negative 与 abstain
语义，并分别记录 metric eligibility 与 abstain reason；一条轴 abstain 不能把另一条已有真值从
指标中静默删除。Exact 与 routing-hint 的 P/R、Wilson 下界、FP/FN 门槛也分别冻结，critical
negative 则只属于 exact 轴且必须与配置的 critical strata 双向一致。不能用一段不完整的自由文本
代替这些边界。Contract、source、review chain、gate 任一变化都生成新 fingerprint，旧
receipt/consensus 不跨版本复用。

数据角色必须保持分离：

| Role | 当前用途 | 可否直接激活 |
|---|---|---|
| `development_regression` | 已暴露 bad-case 与回归 | 否 |
| `independent_blind_challenge` | candidate freeze 后的定向挑战 | 只提供迁移证据 |
| `production_prevalence` | 带冻结抽样设计的自然分布估计 | 只提供迁移证据 |

Stage 1 只实现 closed schema、duplicate-key-safe loader、canonical fingerprint 和只读 coverage
report，不提供通用 selector、blind packet/receipt/consensus CLI 或 candidate runner。现有
lifecycle holdout v1 仍是唯一 post-seal runner，旧 `tag-truth-v1`、
`tag-retrieval-truth-v2` 和 observation-v1/v2/v3 均保持冻结。
`independent_blind_challenge` 与 `production_prevalence` 在合同中只保留角色名；Stage 1 loader
会 fail-closed 拒绝这两类 manifest，直到后续阶段存在外部 selector、seal 和版本化 verifier。

Coverage report 读取 Feature Routing Golden、48-case development Truth、RelationalStore v1 及其
diagnostic baseline，但 baseline 只提供风险测量状态，不能生成标签。它必须按 Tag 分别报告
synthetic、development、adjudicated、blind、prevalence、family 和 Parser-risk availability；
未覆盖 Tag 固定为 `not_qualified`，不得从 macro/micro 分母静默删除。操作合同见
`tests/evaluation/tag_truth_v2/README.md`。

### 6.3 EVAL-01B Stage 2A：无标签选样可构造性与通用盲审 packet

Stage 2A 实现的边界只到“候选运行之前的通用基础设施”：无标签 selection
verifier、针对冻结 policy/exposure 边界的 fail-closed policy-sized structural
selection-capacity lower-bound assessment，以及从已验证 source revision 构建的
path-redacted candidate-blind full-file dual-axis review packet。本阶段没有生成
真实 selection/packet，也没有 label、receipt、consensus、post-seal runner 或激活结论。
它不修改 Matcher、Tag/Dimension/RQ 配置或旧 Truth。

独立性边界不能由 schema 或名称制造。候选开发已暴露于
`applications_app_samples@8255a2987f70317cc3a2a4d46044c6b55f092bb3` 整棵 tracked tree；
该 revision 无论如何重分层都不是 blind。截至本阶段现场盘点，本地没有已登记且可用的
strict descendant，因此真实 blind selection 状态必须是 `not_constructible`，对应 Tag 的证据
仍是 `not_qualified`。未来只能在登记一个满足完整 exposure 和 development
family/path/content 排除边界的 strict-descendant revision 后，由独立 dataset custodian
在候选团队外准备和封存 selection。Verifier 只能拒绝不满足的输入，不能用暴露样本补齐
缺口。

容量下界只计 regular Git file、safe path、非空 UTF-8、unique content 和保守的互不包含
family 集合，宁可少报也不允许重复 content/嵌套 family 造成假容量。下界达到 policy 总 case
数时也只报告 `inventory_capacity_only`，proxy-stratum
capacity 固定为 `not_measured`，因此不能把 preflight 写成“完整 selection 已可构造”。Selection
中的 proxy strata、rank 和 constructibility count 只是挑战样本 coverage
control，不是 exact/routing Truth，不能直接进入 P/R 分子或分母。它们即使来自 import、
call 或 symbol 信号，也只能说明“值得人工审核”，不能代替 reviewer judgement。

通用 packet 需要同时支持 exact 和 routing-hint 两条独立轴。Routing 需要整文件上下文，
所以每个 review item 展示 opaque identity 和隐去仓库路径的整文件正文，由 reviewer 自己确定
ReviewUnit，而不是接受 selector 预选的 span。Packet 必须隐藏 repository path、source
family、proxy stratum、selection rank、repository revision、原始 source hash 及 candidate
身份、配置、预测、输出和诊断；否则 selector/candidate 信号会污染人工 Truth。该视图是
candidate-blind/path-redacted，不是匿名视图；源码内部标识符仍可能暴露来源。

Stage 2A 在 packet 边界停止；下述 Stage 2B 只补齐 receipt/consensus 的通用 schema/CLI，
没有执行真实人工流程。Stage 2C 再补齐五产物 Git seal 的通用验证基础设施；真实 seal、预期
首次 candidate run 和质量门禁仍属于后续流程。
在这些事实全部完成前，不得把“infrastructure 已实现”改写为“Tag 已验证”。

### 6.4 EVAL-01B Stage 2B：通用双人 receipt 与双轴 consensus

Stage 2B 实现 Stage-2A packet 后的通用人工审核合同，但不运行候选：

```text
self-hashed Stage-2A packet
-> human reviewer A full-case receipt
-> human reviewer B full-case receipt
-> exact/routing dual-axis consensus
```

`tag-truth-v2-review-receipt-v1` 将 self-hashed packet、其中记录的 `selection_id`、目标 Tag
contract、完整 review-policy fingerprint、reviewer/round/blinding 声明和全部 case 决策冻结在
同一 self-hashed artifact 中。
每名 reviewer 必须从隐去路径的整文件正文自行确定 ReviewUnit，再分别判断 exact applicability
与 routing-hint applicability；receipt 不接收 candidate prediction，也不运行 Parser、Matcher 或
FeatureRouter 来代替人工判断。Consensus 恰好消费同一 packet 的两份不同 human reviewer
receipt，并保留两票原始 rationale/evidence。

共识先比较 ReviewUnit identity：kind、qualified symbol 或 inclusive span 任一不一致，整个 case
的两条轴都 unresolved。Unit 一致后 exact 与 routing 分轴处理；一轴 disagreement/abstain 不能
让另一轴已经一致的 judgement 消失。两位 reviewer 对 taxonomy abstention 达成一致时记录
`agreed_abstain`，它仍是非指标 blocker，不能转成 negative 或从 campaign 删除。

CLI 语义冻结为：

- `tools/seal_tag_truth_v2_review_receipt.py`：成功封存合法 receipt 返回 0，非法输入返回 2；
- `tools/build_tag_truth_v2_consensus.py`：完整且无 unresolved/abstain 返回 0，合法但存在任一
  unresolved axis 或 `agreed_abstain` 返回 1，非法 schema/binding/coverage/reviewer 输入返回 2。

返回 1 代表成功保存了可审计的未决共识，不是 artifact 损坏。Consensus 只定义
`complete/unresolved` 审核状态，不定义 release 或 activation 字段；即使
`consensus_status=complete`，也只证明两名 reviewer 完整一致，不证明样本独立、near-duplicate
合格、证据质量达标、candidate 行为正确或可以激活。

当前没有真实 selection、packet、receipt 或 consensus；也没有满足暴露边界的已登记
strict-descendant revision。Selection/review policy 仍未批准，external selector/reviewer identity
仍是未认证声明，near-duplicate qualification、Git seal 和 post-seal first candidate run 均未
完成。因此当前 Tag evidence 保持 `not_qualified`。本阶段不修改 Matcher、默认
Tag/Dimension/RQ、Parser、Golden 或组合 fingerprint，也不执行 candidate。

Stage-2B CLI 会验证 packet 自哈希，并把 receipt 绑定到 packet 内记录的 `selection_id`；它不接收
外部 Stage-2A selection artifact，因此不会在本阶段重新验证 selection/checkout provenance。
该 provenance bridge 仍属于后续独立阶段。

后续必须分阶段补齐：consensus 到 `TagTruthV2Suite` 的 publication bridge、版本化
near-duplicate qualification、真实外部 policy/selection 与 seal、sealed first-run runner、质量
门禁计算和独立 activation 决策。任何一个后续步骤都不能由 `complete` consensus 自动替代。

### 6.5 EVAL-01B Stage 2C：五产物 provenance 与 Git seal 验证

Stage 2C 不改变 Stage 2A/2B artifact，而是在人工审核完成后重新验证完整链：

```text
Selection + Packet + Receipt A + Receipt B + Consensus
-> exact Git seal tree
-> standard-library-only preflight
-> typed source/artifact chain rebuild
-> tag-truth-v2-provenance-verification-v1
```

`tools/verify_tag_truth_v2_git_seal.py` 要求指定完整小写 seal commit 和恰好五份 artifact。Preflight
在 import 任何项目模块前验证 project Git top-level、`HEAD == seal_revision`、clean worktree、
candidate commit 是 seal 的严格祖先，以及五条路径唯一、仓内、regular、non-symlink、已提交。
每份当前 bytes 必须与 `git show seal:path` 完全一致；Git ancestry/tree/blob 检查禁用 replace
objects、关闭本地 commit-graph cache，并拒绝 project/source Git common directory 中的 legacy
`info/grafts`。只有这些条件通过后，typed verifier 才解析本次捕获的内存 bytes，并重新执行：

- Selection self-hash、development Truth exclusions、source HEAD/remote/clean/blob/hash/line-count；
- exposure strict-descendant 及整棵 exposure tree 的 path/family/blob 边界；
- 从 Selection + verified checkout 重建 Packet；
- 两份 Receipt 的 binding、完整 case coverage 和 reviewer/round 独立性；
- 从 Packet + Receipt 确定性重建 Consensus。

Seal 证明五份 artifact 同时存在于指定 tree，并不证明它们都由该 commit 引入，也不限制该
commit 的其他 diff。Preflight 会同时逐字节验证 frozen typed-verifier closure，拒绝其中的
symlink、bytecode cache、同名模块和顶层 import shadow；CLI 在 typed import 前移除仓库内其他
Python 搜索路径，并强制以 isolated mode (`-I`) 启动，防止脚本目录、当前目录或 `PYTHONPATH`
在 preflight 前覆盖标准库。Python startup、解释器、标准库和 site-packages 仍是明确的 host
trust boundary，不在 seal 内。

输出 report 冻结 seal revision/tree、source revision/tree、五份 artifact 的路径、Git blob、原始
byte SHA-256 与全部逻辑 ID。Report 在 seal 后生成，不声称包含自己的 seal commit；如需留存，
只能进入后续审计 commit。完整 consensus 返回 0，合法但 unresolved/abstain 返回 1，任何
schema、Git、路径、checkout、byte 或 binding 错误返回 2。返回 0 仍只表示受检 provenance
完整，不表示 evidence qualified 或 candidate ready；report 固定记录 evidence `not_qualified`、
candidate `not_run`。

Stage 2C 没有生成真实 selection/packet/receipt/consensus/seal，也不验证 reviewer/selector 身份、
Git remote 真实性、host 或“首次运行”。多次 filesystem/Git 检查仍存在 host-level TOCTOU
边界，因此正式使用要求全新、独占、最好只读的 checkout，后续 candidate runner 还必须自行
重验。Near-duplicate、policy approval、`TagTruthV2Suite` publication、candidate runtime/
environment/harness、P/R、质量门禁和 activation 均不属于本阶段。

### 6.6 EVAL-01B Stage 2D1：版本化 near-duplicate shadow screening

Stage 2D1 只补齐一个 post-seal、pre-candidate 的影子筛查层：

```text
Stage-2C five-artifact provenance rebuild
-> candidate/seal-frozen shadow policy
-> agreed ReviewUnit + full-file extraction
-> pinned reference inventories
-> deterministic two-channel token/shingle comparison
-> self-hashed tag-truth-v2-near-duplicate-screening-v1 report
```

筛查器同时检查两条独立污染轴：exact Truth 使用 reviewer-agreed ReviewUnit；routing Truth 使用
整文件。Reference 不是只取旧 48 case，而是覆盖 candidate project commit 的完整 tracked text、
source exposure revision 的完整 tracked text、development Truth source 在其原 revision 的 bytes，
以及 campaign 内其他 case 的 Unit/文件。Git blob 可去重计算，但每条 path/revision provenance
必须保留。含 NUL 的 binary blob 不属于 `all_tracked_utf8_text`，必须显式计数并进入 inventory
fingerprint 后才可排除；non-regular、oversize、non-UTF-8、invalid/unterminated token 或其他未评估
输入必须计数并进入 blocker，不能静默跳过后声称 qualified。

算法不调用 Parser v1。`lexical-content-v1` 丢弃空白和注释，保留 keyword/operator/identifier，
并将字符串、数字和 template literal 归一化；`lexical-shape-v1` 再抽象普通 identifier，只能触发
灰区。比较量冻结为 7-token content shingle、11-token shape shingle、双向 containment、对称
Jaccard、content/shape normalized token-stream equality 和最长连续 token run；连续覆盖率也同时
检查 selected/reference 两个方向，防止旧文件或旧 Unit 被嵌入更大的新文件后因分母稀释而漏检。
阈值以整数分子/分母比较，避免浮点边界漂移，shingle hash 命中不能代替 canonical token tuple
核对。Gray content 至少要求 16 个共享 shingle，避免一个 7-token 小片段把大文件拖入灰区；
Shape equality/containment 只能进入灰区，不能单独 hard reject。

比较前还会按 `nfc-character-work-v1` 冻结并记录 probe 数、selected NFC 字符数、unique reference
NFC 字符数、eligible pair 数以及 pair 两侧 NFC 字符总量。当前 shadow 上限分别是 16,000,000、
64,000,000、2,000,000 和 250,000,000，最多记录 10,000 个 match。预估超限时不做任何 pair
比较；运行中若 match 记录超限，则丢弃全部 partial match。两种情况都会令所有 case axis abstain，
报告 `attempted_similarity_pair_count` 和 resource blocker，绝不把已评估子集写成 `clear`。
`probe_evaluation_status=not_run_resource_limit` 时 token/shingle count 的 0 表示“未运行”，不是实测
为零。Inventory 不完整或任一 reference tokenizer 失败时，同样只有已发现的 duplicate/gray 可以
保留；其余 axis 必须 abstain。

当前 `near_duplicate_shadow_policy_v1.json` 明确是 `snapshot_only_not_approved`：hard/gray
阈值只是 calibration seed，不是已证明的 P/R 门禁。Policy 必须在 candidate commit 已存在，且
candidate 与 seal commit 的 Git blob 完全一致；screening core、preflight 和 CLI 也必须在 candidate/
seal 保持同一 blob，防止看到 blind selection 后通过调参或改实现语义。完整 screening report 绑定
Stage-2C verification ID、seal/source/candidate tree、五件套逻辑 ID、policy 和 reference inventory
fingerprint，并固定 overall evidence `not_qualified`、candidate `not_run`。
`screening_id` 的 self-hash 只证明 JSON identity；正式消费必须调用完整 verifier，用 policy、三份
pinned inventory 和五件套重新构建逐字段相等的 report，不能把 parse/self-hash 当语义验证。

当前 shadow policy 的单 blob 上限是 2 MiB，而本项目当前 commit 中冻结的 generated
`third_party/tree-sitter-arkts/src/parser.c` 为 24,144,840 bytes。因此任何以当前项目 tree 作为
candidate project reference 的真实报告都会得到 `candidate_project:oversize_entries` 并至少是
`review_required`。这是当前 shadow policy 的有意保守 abstain，不是 near-duplicate clean 已可用；
后续只有在独立资源基准和人工 pair Truth 完成后，才能通过新 policy 版本选择流式扫描或调整上限，
不得在看到 blind sample 后临时排除该文件或放宽阈值。

对同一 `fdac0fcc2a003f4aa1e4e00aac88b871f7ba602a` tree 的只读盘点还得到 554 个 tracked
entry、552 个已加载 UTF-8 document、549 份 unique text；candidate-project path text 合计
15,161,916 个 NFC 字符。因为 scope 是全部 tracked UTF-8 text，而 tokenizer 使用 ArkTS-like
引号/comment/template 规则，54 个 Markdown、Python、JavaScript 等 document 被标为 tokenizer
issue。因此当前真实 tree 除 oversize 外还必有 `reference_tokenization_issues`；若按 24 case ×
file/Unit 共 48 probe 估算，仅 candidate-project reference 一侧就超过 7.27 亿 pair NFC 字符，也会
触发当前 2.5 亿预算。三者都是可见 abstain，不是质量通过。正式 policy 必须先解决 media-aware
tokenization/streaming 与资源基准，不能为了得到 clean 临时忽略非 `.ets` 文件。

CLI 的 0/1/2 语义保留为：0 只允许未来 approved+calibrated policy 的无 blocker 结果；当前合法
shadow clear、duplicate、gray、短 Unit、inventory blocker 或 unresolved 都写出报告并返回 1；
schema、Git、path、policy freeze、artifact binding 或 rebuild 错误返回 2。Stage 2D1 没有运行真实
campaign、没有创建人工 pair Truth，也不实现 `TagTruthV2Suite` publication、candidate runner、
P/R、quality gate 或 activation。要批准正式 policy，仍需独立双审冻结 duplicate、independent、
ambiguous pair Truth 后另开版本；不能根据本次 blind sample 调低阈值。

CLI 会清除继承的 `GIT_*` 重定向/配置变量，并显式关闭 `core.fsmonitor`，防止只读 Git 检查被
环境或 repository fsmonitor 命令改写。它仍信任本机 `git`、`PATH`、受保护的 Git config 和独占
checkout；`git status` clean 也不能单独证明 assume-unchanged/skip-worktree 文件逐字节一致，
所以关键 artifacts、policy 和 verifier closure 仍必须单独按 Git blob/bytes 复核。外置 Stage-2C
report 会校验打开前后 device/inode，并通过同一 nonblocking regular fd 最多读取 16 MiB + 1
byte。Inventory entry cap 仍是在 `git ls-tree` 输出返回后检查，NFC work
cap 也发生在 sealed JSON/Git blob 已加载之后；它们保护 comparison 阶段，不是 OS 级输入内存
sandbox，超大受信仓库仍需受控容器/资源限制。

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
