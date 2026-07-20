---
title: 10 评测与反馈闭环模块
status: canonical
implementation: partial
updated: 2026-07-20
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
- 36-case Retrieval Golden；strict baseline 与 require-perfect 均通过。
- 通用 Tag Truth 的 immutable contract、无标签 selection、candidate-blind review packet、
  两轮独立人工 Receipt、Consensus、五产物 Git/provenance seal、近重复 shadow screening 和
  consensus publication 合同。
- Near-duplicate Pair Truth、exhaustive Oracle、component-aware calibration、
  PolicyCandidateFreeze、HoldoutReleaseReceipt 和 typed-artifact full-rebuild approval 合同。
- Evaluation-only 的多 ReviewUnit AI Tag shadow 诊断合同：逐 Unit 绑定 Card、full-24
  Request/Envelope 和 non-formal ResponseValidation，并以内容寻址 report 汇总 status/source、
  decision/comparison、逐 Tag 分布、reported usage/latency；完整 verifier 从调用方 roots 重建全部
  Unit/report。该链不 dispatch、不构造 formal Result/Outcome/Hybrid、不进入 Retrieval，固定
  not-qualified；当前 input set 未绑定 sealed campaign manifest，Card 上游 provenance 也不在该
  verifier closure。
- Evaluation-only 的多 ReviewUnit shadow campaign 准备合同：从调用方提供的
  `AnalysisResult + ContextPlanResult + ChangeSet + SourceSnapshotBundle` 与显式 Unit selection
  重建每个 Card/ModelView/full-24 Request/Envelope/Plan，生成内容寻址 manifest 和不含源码、Prompt、
  wire body、credential 或 response 的 inspect-only 投影；full verifier 会从上游 roots 重建整个
  campaign。campaign-aware adapter 要求 caller-keyed Plan ID coverage 完整，并验证每个既有
  ResponseValidation 绑定对应 Envelope，再复用上述 v1 evaluator；Validation 不绑定 Plan execution，
  adapter 不 dispatch、不读取 credential，仍固定 not-qualified，也不改变 v1 report 的
  `caller_supplied_input_set_not_campaign_bound` 语义。
- 多 ReviewUnit shadow campaign execution 合同：从完整 upstream roots 重建 Campaign，要求每个 Plan
  都有唯一 runtime binding，并按 canonical Plan 顺序逐个执行；每个 Plan 最多一次 attempt、固定无重试。
  真实固定 HTTP transport 与 injected test transport 默认都禁止，必须分别显式 opt-in 才会 dispatch。
  每个 Unit 分开记录 `attempted/skipped_budget/not_run`，attempted 再区分 valid、inner-invalid、
  outer-invalid、4xx/429/5xx、timeout、transport 和 response-too-large，不会把缺失或失败判断伪造成
  24 个 negative。零 attempt 由独立内容寻址的 local non-attempt receipt 绑定 reason/control stage；
  内容寻址 Result 从 Unit 明细重建 counts；persistent verifier 核对完整运行 artifact
  graph，可选 caller-owned raw response bytes 再触发单 Plan raw-byte full rebuild。该 Result 固定
  not-qualified、shadow-only，不进入 Hybrid/Retrieval/Evidence/Finding；Execution Result 自身不是
  provider/runner signature。
- 固定 repository-synthetic multi-Unit smoke 入口：hash-locked 的 4-ReviewUnit 合成 Campaign 可经默认
  inspect-only CLI 查看 metadata。Campaign 的 base/head `file-analysis-v1` 来自随 wheel 打包且整包
  hash 校验的冻结 snapshot；构建、Card replay 和 verifier 均不读取 `ARKTS_PARSER_*`，也不启动 Parser
  子进程。执行必须精确绑定 Campaign ID、Plan-set digest、全部 caps、固定确认文本和 per-Plan 原子本地
  marker。自动化测试只用 injected transport；valid-shape Unit 的 24 项 `tag_id + decision` 会连同
  `source_role/unit_kind/unit_symbol` 写入带 self-hash 的 `0600` 安全 summary artifact，reason、证据行、代码、
  路径、Prompt/body、raw response、credential 和 state path 均不写入。该 summary 不是完整 evidence graph，
  也不接受任意真实代码 Campaign。2026-07-20 已对这份固定 synthetic Campaign 执行一次受控 live：
  4 个 Plan 各 attempt 一次、无重试，4/4 均为完整 24-Tag `valid_shape`；这只是历史连通性/形状观察，
  不是 formal evidence 或质量评测。
- Attempted-Plan Formal Execution V2 合同：公开 authority 入口只接受 Plan/Claims、一次性 capability 和
  Envelope；集成 runner 自建固定 HTTP/TLS transport，并通过私有 verified sink 捕获同次运行的完整
  shadow artifacts 与原始 bytes 后做 full rebuild；确定性生成可选 Result V2、
  Outcome V2、signed Subject、Hybrid V2 和 Ed25519 runner attestation。`analysis_run_id` 绑定
  `Plan + Attempt`，formalization event 只是签名 nonce，不是 provider run/time proof；provider evidence
  scope 会区分“完整 HTTP response observed over TLS”与“固定 transport attempt 但无完整 response”。
  只有外部 pinned registry 与 complete-evidence verifier 返回的不可序列化 eligibility 才能被下游消费，
  standalone self-hashed JSON 或 post-hoc caller-supplied artifacts/raw 不够。零 attempt
  `skipped_budget/not_run` 仍只属于 Campaign audit。合成测试 monkeypatch 固定 transport 只证明合同，
  不证明真实 TLS/provider identity；部署 signer/KMS 与进程隔离仍不存在。
- Retrieval V3 受信 construction gate：closed schema 绑定 Formal V2 Hybrid/Outcome/Result、Subject 和
  attestation identities；Builder 从 `AnalysisResult + ContextPlanResult + SourceSnapshot` 重建 v1 baseline
  与 Cards，要求每个 Unit 的完整 formal evidence 通过同一个 pinned-registry verifier，只投影 verified
  positive，返回不可序列化 wrapper。标准 `RetrievalService` 仍只接受 v1。
- Phase C Retrieval shadow 合同：独立 `retrieval-shadow-policy-v1` 绑定 v1 config 和五个固定 pool；
  `RetrievalShadowServiceV3` 以 exact-type gate 只接受 `VerifiedRetrievalRequestV3`，保留同次标准 v1
  control EvidencePack，并让 `static_vector` 与 `hybrid` 在同一 candidate/rank/weight 账本上执行加权
  RRF。AI positive 只在 `ai_inferred` pool，keyword 只在 `text_keyword` pool，code vector 不拼 Tag；
  candidate Dimensions 只作诊断。control 仍是标准 `evidence-pack-v2`；实验结果是独立
  `retrieval-shadow-result-v1` audit artifact 和不可序列化 runtime wrapper，不冒充 EvidencePack，固定
  shadow/not-qualified/non-user-visible/non-prompt/non-Finding。wrapper 以独立 construction snapshots
  绑定完整 shadow/control 内容，拒绝重新 self-hash 的 semantic score、diagnostics 或 control Clause
  替换，且公开访问不会重复调用 embedding provider。
- Phase D0 文档 Truth 与双臂评分合同：`retrieval-document-truth-v1` 逐 Unit 内容寻址调用方提供的唯一
  `required/acceptable/irrelevant/forbidden` Clause 标签、critical Dimensions、目标平台、Feature Config
  和固定 Index；同一 source locator 不得通过更换 `rule_id` 或其他元数据重复计数。
  `retrieval-shadow-evaluation-v1` 从 Truth、V3 request、shadow result 与 Index 四个 caller roots
  确定性重建预算前/预算后、`static_vector/hybrid` 的 K=1/3/5/8 指标、required MRR、Knowledge gap、
  forbidden/applicability、token、degraded 和 paired delta，并按 development/calibration 分别聚合。
  paired quality delta 只消费 eligible Unit；hard-safety observation 不因 degraded 被隐藏。未标注 observed
  Clause 会 fail-closed；报告固定 `serialized_audit_only`、offline/not-qualified/non-user-visible/
  non-prompt/non-Finding。当前只实现合同和合成测试，self-hash Truth 不是人工来源证明、consensus 或
  blind seal，full verifier 也不恢复 Shadow runtime/policy authority。development/calibration 只是调用方
  提供的 split 标签；v1 没有 rule-family/leakage-component 或 family seal，分开聚合不证明不存在近重复泄漏。
- 提交 `a83eeb6` 的合成/负向验证：D1b-1 targeted `28 passed`、Stage 2A～2D2a 相关
  `294 passed`、全量 `1196 passed / 3 skipped`；这些是该提交上的运行快照，不是长期
  machine attestation。
- 4 个固定 revision 的代码语料来源：`arkui-ace-engine`、`xts-acts`、
  `applications-app-samples`、`codelabs`。
- 11 个知识来源和 4 个分析工具的来源登记，可用于后续分层评测和结果追溯。

缺失：

- 真实通用 Tag blind campaign、production-prevalence Truth 和总体 Tag Precision/Recall。
- 真实代码的 multi-Unit DeepSeek campaign、人工 Unit-exact Truth、重复运行稳定性和模型 Tag P/R；
  当前固定合成样例的单次/多 Unit valid-shape observation、Campaign execution 合同与 inspect-only
  manifest 都不能替代这些证据；当前没有任意真实项目代码 Campaign 的 multi-Unit live CLI。
- provider signature、部署 provision 的 runner private key/registry、KMS/HSM/rotation、真实 formal live
  artifact、外部授权 attestation、source Git provenance attestation、生产预算 ledger 与部署合规证明。
  当前实现的 Ed25519/pinned-registry 代码和合成 tamper tests 只证明密码学与 fail-closed 合同；Campaign
  Execution Result 仍只保存本地 process/runtime observation，2026-07-20 历史 safe summary 也因缺少 raw
  bytes、完整 evidence graph 和当时 attestation 而不能事后追认为 Formal V2。
- 真实 near-duplicate Pair Truth、经过校准批准的 policy 和 screening v2。
- 面向真实应用的 Context/Retrieval relevance Truth 仍不足。
- Phase C/D0 只有合成/fixture 合同证据；虽然独立文档 Truth schema 和评分器已经存在，仍没有真实
  人工 Clause Truth、真实 static_vector vs hybrid Recall/Precision、production prevalence 或生产
  PublishedKnowledgeBuild。即使使用 publication-origin index，shadow/evaluation artifact 的资格也固定
  不变。
- Rule precision 数据、Final Finding 人工标注和最终评审闭环。
- 跨全流水线的统一生产运行证明 artifact、评测数据库、外部身份认证和跨模块最终质量门禁。

## 3. 分层评测

| 层 | Golden 输入输出 | 核心指标 |
|---|---|---|
| Input | diff -> ChangeSet | ChangeAtom 覆盖率、行号和变更类型准确率、base/head fidelity |
| Parser | source -> Facts/Declarations | precision/recall、degraded rate |
| ReviewUnit owner | ChangeSet+Facts -> Primary Units | owner precision/recall、changed-line coverage、诊断传播、确定性 |
| Context Planner | Primary+relations+budget -> ContextPlanResult | required-context recall、relation precision/recall、distractor rejection、预算合规 |
| Feature Routing | UnitFactScope -> Tags/Dimensions/Questions | exact/routing Tag precision/recall、Unit/MR Dimension precision/recall、串扰率、问题绑定覆盖 |
| AI Tag shadow campaign preparation | upstream graph + Unit selection -> manifest/inspection/per-Unit Plans | selection/identity/rebuild 完整性、跨 Unit splice 拒绝、安全投影；不执行 provider、不计算 P/R |
| AI Tag shadow campaign execution | manifest + trusted upstream + per-Plan runtime bindings -> per-Unit executions/result | canonical sequential 单次无重试、attempted/skipped/not-run、non-attempt receipt 与 inner/outer/provider failure 状态矩阵、persistent graph rebuild、可选 raw-byte full rebuild；固定 synthetic CLI 只证明控制合同，不计算 P/R、不进入 Hybrid/Retrieval |
| AI Tag Formal Execution V2 | attempted Plan evidence + raw response（若有）+ pinned runner registry -> opaque eligibility | Result/Outcome deterministic projection、Plan+Attempt run identity、状态化 provider scope、Ed25519 Subject attestation、full rebuild/tamper rejection；不覆盖零 attempt，不证明 provider signature/Git provenance/部署 key 或质量 |
| Retrieval V3 construction gate | AnalysisResult/ContextPlan/Snapshots + per-Unit formal evidence -> verified non-serializable wrapper | v1 baseline/Card replay、formal evidence exact coverage、仅 verified positive 投影；裸/serialized V3 没有执行 authority |
| Phase C Retrieval shadow | exact `VerifiedRetrievalRequestV3` + pinned index/policy -> v1 control EvidencePack + verified shadow result | 五 pool、两 arm 同账本、scope/RRF/budget/identity、序列化降权和固定资格；只证明 shadow 合同，不证明真实文档质量 |
| Phase D0 document evaluation | caller-supplied Clause Truth + V3 request + shadow result + fixed index -> serialized-audit-only offline report | pre/post-budget 双臂 Recall/coverage/Precision/MRR、Knowledge gap、critical Dimension、split aggregates、eligible-only quality delta 与 all-observed hard-safety delta；未标注候选拒绝，split 无独立 family seal，固定 not-qualified，不证明 Truth/Shadow runtime authority |
| AI Tag shadow diagnostic | Cards + ResponseValidations -> Unit/report artifacts | valid_shape/invalid_output/unavailable_claim、positive/not_supported/abstain、exact×validated-content 分布、逐 Tag counts、reported usage/latency；无 Truth 时不计算 P/R |
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

tests/evaluation/
└── tag_truth_v2/             # Tag Truth 选样、双审、seal、近重复、校准和 publication 治理
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

历史 `fdac0fcc2a003f4aa1e4e00aac88b871f7ba602a` 快照曾记录 554 个 tracked entry、54 个
tokenizer issue，以及 48-probe 只计算 candidate-project reference 就约 7.27 亿 pair NFC 字符；
这些数字只描述旧 tree。D1b-1 开始实现前冻结的
`d16b5f9d9bbac7040af9d315e52a98c016197d33` 只读快照得到 563 个 regular blob、561 个已加载
UTF-8 document、560 个 unique blob、558 份 unique text、56 个 tokenizer issue；同一 48-probe
口径的 candidate-project reference lower bound 是 743,761,104 pair NFC 字符。该值是 pinned
历史证据，不是滚动的 “current HEAD” 指标；任何正式 campaign 必须对实际 candidate commit
重新计算 inventory。已知 oversize、tokenizer 和 work-budget 风险仍必须显式 abstain，不能为了
得到 clean 临时忽略非 `.ets` 文件。

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

### 6.7 EVAL-01B Stage 2D1b-1：Pair Truth 与校准治理合同

Stage 2D1b-1 只增加 near-duplicate 校准所需的 closed schema、self-hash、builder 和 full
rebuild verifier，不修改 Stage 2D1 shadow 算法或 policy。合同链是：

```text
component-aware PairSelection
-> path-redacted PairReviewPacket
-> human Receipt A + human Receipt B
-> PairConsensus
-> exhaustive canonical OraclePredictionSet
-> frozen CalibrationGate
-> PolicyCandidateFreeze
-> HoldoutReleaseReceipt
-> CalibrationReport
-> typed-artifact-chain-verified PolicyApprovalReceipt
```

Pair Truth 只允许 `duplicate / independent / ambiguous`。Selection 根据 member identity、原文
hash、normalized-body hash、template cluster、source family 和人工 related group 计算 leakage
component；任何连通 component 跨 `calibration / acceptance_holdout` 都 fail-closed。Packet 不包含
split、component、selection rank/stratum、显式 repository/path 或 policy output；但完整源码仍可能
通过 identifier/import 暴露来源，所以它只是 path-redacted/candidate-blind，不是匿名材料。两份
Receipt 必须来自不同 reviewer、不同 round，并完整覆盖同一 packet；Consensus 保留两票各自的
label、evidence line 和 rationale，不一致的 Pair 不进入二元指标。

Exhaustive canonical Oracle 对 manifest 中每个 Pair 按冻结的方向规则直接调用 Stage 2D1 v1
canonical similarity，不使用 candidate prefilter：file/file 和 Unit/Unit 双向比较，Unit/file
只比较 Unit 到 file，再以 `duplicate > gray > abstain > clear` 聚合。它的
`oracle_semantics_fingerprint` 只标识这组声明语义，不是 Python 文件或 import closure 的代码
identity。`PolicyCandidateFreeze.verifier_closure` 当前也只是调用方提交的 Git blob 清单；本阶段
没有 standard-library preflight 去读取 candidate commit、核对这些 blob 或证明当前 checkout
字节一致。未来正式 qualification 必须补齐该 preflight，不能把 semantics fingerprint 或一份
self-hashed closure 声明当成代码冻结证明。

Calibration report 同时保存 calibration/acceptance-holdout 两套 confusion、P/R、Wilson lower
bound、duplicate-block recall、ambiguous guard，以及双审 raw agreement/Cohen kappa。Gate 只根据
acceptance holdout 决定 `passed / failed`；component ID 会随每个 case 进入 report，并同时要求
duplicate/independent 各至少 80 个独立 component、每个二元指标 Pair 独占一个 component。这样
Wilson 的 Pair 分母在二元验收集合中与 component 数一一对应，不能用同一 component 的相关 Pair
虚增样本；但它仍不是针对任意聚类结构的 component-adjusted effective-sample-size 模型。

`PolicyCandidateFreeze` 先绑定 policy candidate、Oracle semantics、gate、candidate commit 和声明
closure，并固定 `acceptance_holdout_labels_seen=false`；`HoldoutReleaseReceipt` 再绑定同一 selection
与 freeze，并要求 `released_at > frozen_at`。正式 Approval receipt 必须在完整重建 Selection、
Packet、两份 Receipt、Consensus、Oracle predictions 和 CalibrationReport 后，再核对
freeze/release/report/approver 的全部绑定；单独 parse report/receipt 或重算 self-hash 不构成
verified approval。这里的 full rebuild 从已提供并通过 self-hash 校验的 PairSelection 根开始，
只验证 typed artifact chain；它不验证 Pair source Git provenance，也不验证声明的 verifier
closure blob 与 candidate/current checkout 字节相同。

Holdout custodian 不得同时担任 Pair reviewer；policy approver 不得是 reviewer 或 custodian，
审批证明和记录时间不得早于 holdout release，当前秒级 timestamp 合同允许相等。failed 或
`not_eligible` 的 report 不能支持 `decision=approved`。即使 PolicyApprovalReceipt 的
decision 为 approved，其 scope 也仅是
`future_verified_near_duplicate_screening_policy_semantics`：它不批准当前 v1 policy、不激活
screening，也不会使 Pair Truth 或 Tag Evidence qualified。

Selection 对 selector/process 的来源声明，以及 reviewer/custodian/approver identity 和时间声明，
目前都只是 artifact 内自证，没有外部身份认证、签名、Git-host attestation 或可执行的组织隔离
证明。因此即使
`calibration_gate_status=passed`，也只表示 `eligible_for_human_review`；Report 自身固定
`policy_approval_status=not_approved`，整体 evidence 继续是 `not_qualified`。

仓库当前没有真实 PairSelection、Packet、双审 Receipt、Consensus、PolicyCandidateFreeze、
HoldoutRelease、CalibrationReport 或 ApprovalReceipt。本阶段不批准真实 policy，不实现 policy v2、
screening v2、publication v2，不运行真实 campaign/candidate，也不改变 Stage 2D1/2D2a v1、
Tag/Dimension/RQ、Parser、Matcher、Golden 或 Feature config fingerprint。

本阶段没有 CLI。合成合同和负向行为可用以下命令复核：

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_tag_truth_v2_near_duplicate_calibration.py \
  tests/test_tag_truth_v2_near_duplicate_oracle_semantics.py
```

提交 `a83eeb6` 上该命令为 `28 passed`。它证明 schema、full rebuild、fail-closed 门禁与
Oracle 语义，不证明真实 Pair P/R、人员身份、holdout 访问控制或生产质量。

Stage 2D1 之后存在两条并行治理分支，不是串行依赖：

```text
Stage 2D1
├── Stage 2D1b-1  Pair Truth / calibration governance for a future policy v2
└── Stage 2D2a    consensus publication for the existing v1 screening chain
```

### 6.8 EVAL-01B Stage 2D2a：双审共识的版本化发布

Stage 2D2a 新增独立的 `tag-truth-v2-publication-v1`，不放宽 Stage 1
`TagTruthV2Suite`。它在 candidate 运行前消费：

```text
sealed 五件套
+ Stage-2C full rebuild
+ Stage-2D1 policy/inventory/full rebuild
-> published_consensus_not_qualified | blocked_no_suite
```

只有 complete consensus、全部 ReviewUnit/两轴均 resolved，且每个 case 的 file/Unit
near-duplicate 决策均为 `clear` 时，publication 才包含
`tag-truth-v2-published-consensus-v1`。该 suite 原样保留两名 reviewer 的完整 vote，包括每票各自
的 label、evidence line、rationale 和 ReviewUnit，并同时保留 consensus 合并后的 exact/routing
结果。Suite 的 `chain_binding_id` 自哈希五件套、seal、candidate freeze、Feature config、
Stage-2C/2D1 ID、source/exposure tree 和三份 inventory summary，不能脱离 lineage 单独换壳。
Proxy stratum 仍只是选样元数据，不是正负 Truth。

若 consensus disagreement/abstain，或 screening 为 duplicate、gray、resource/tokenizer/inventory
abstain，则输出 `blocked_no_suite`，不得删除 case 后发布剩余子集。Schema、Git、path、hash、tree、
artifact 或 full rebuild 不一致属于非法输入，不生成 publication artifact。

本阶段不臆造 sealed 输入没有定义的 critical-negative、normalized body、template cluster 或 gate
policy，也不运行 Parser、Matcher、FeatureRouter/candidate，不计算 P/R/Wilson，不写
`activation_ready`。Published/blocked 两种状态都保留同一个 readiness envelope：evidence
`not_qualified` 及其 selection/review/near-duplicate/外部身份 blocker、candidate `not_run`、
quality gate/activation `not_evaluated`。

`tools/build_tag_truth_v2_publication.py` 必须使用 `-I -B`。Preflight 先完整复用 Stage 2D1
preflight，再验证 publication core/preflight/CLI 在 candidate/seal 的 Git blob 相同，并通过有界
nonblocking regular fd 捕获外置 screening report；typed 层重新构建 Stage 2C、重新扫描三份
inventory 并重建 Stage 2D1 后才能投影 Truth。返回 0 只表示成功生成
`published_consensus_not_qualified`，返回 1 表示合法 `blocked_no_suite`，返回 2 表示输入或重建
非法；0 不代表真实 Tag 质量合格。`publication_id`/suite fingerprint 只证明 JSON identity，正式
消费仍必须调用 full verifier 重建 sealed/source/inventory 输入。

当前仍没有真实五件套 campaign，且 Stage 2D1 的已知真实 tree blocker 不变，所以 Stage 2D2a
只补齐 publication 合同，不产生真实 P/R 或 production-qualified Tag。

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

现有 36-case Golden 与 `evidence-pack-v2` 仍只覆盖标准 v1 request 合同。Phase C 的确定性/负向合同应单独验证：

```text
裸 RetrievalRequestV3 / serialized V3 / V3 subclass 均不能执行
五个 pool 必须完整、独立、稳定排序并保留各自 scope
static_vector 与 hybrid 复用同一 pool ledger；前者不得出现 ai_inferred contribution
AI not-supported/negative 不减少 static_vector 候选
text_keyword/ai_inferred 永不获得 unit_exact provenance
candidate Dimensions 不进入 formal coverage 或 budget
V3 vector query 只包含 code + exact facts
同次 v1 control EvidencePack 的内容和 identity 不被 shadow arm 改写
serialized retrieval-shadow-result-v1 只能是 serialized_audit_only
runtime wrapper 不可序列化，并在每次访问时重验完整 binding
重新 self-hash 的 semantic score、diagnostics/degraded 或 control 内容替换仍被 construction snapshot 拒绝
重复访问 runtime wrapper 不得增加 embedding provider 调用
所有 index origin 都保持 not_qualified/non-user-visible/non-prompt/non-Finding
```

这些断言即使全部通过，也只证明 schema、作用域、排名账本、authority 和 fail-closed 合同。真实增量必须
使用冻结 KnowledgeIndex 和独立 Clause Truth，分别报告 budget 前 candidate ranking 与 budget 后 selected
Clauses，并比较同一结果中的 `static_vector` 与 `hybrid`。没有真实 Truth 时不得计算或宣称真实
Precision/Recall；publication-origin index 也不能自动把 shadow artifact 变成 qualified Evidence。

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
V3 request/control EvidencePack/shadow result/policy/index/attestation IDs
逐 pool ranks、weighted RRF contributions 与 static_vector/hybrid selection
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
Near-duplicate acceptance holdout 至少 80 duplicate components + 80 independent components
Near-duplicate 每个二元指标 Pair 独占 component；fatal false-clear/hard-reject/binary-abstain 为 0
Near-duplicate Report passed 不得自动变成 policy approved 或 evidence qualified
高严重级 Rules 不允许已知误报
Retrieval Recall@5 达到基线
Phase C static_vector/hybrid 必须同 index、同 policy、同 pool ledger；除 ai_inferred 外不得改变实验因素
Phase C artifact 必须固定 not_qualified、非用户可见、不可进入 Prompt/Finding
serialized V3 或 serialized shadow result 获得 runtime authority 的数量为 0
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

目标 `config/evaluation.yaml`（尚未实现 loader）：

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

1. 优先执行 `TAG-CORPUS-01`：固定 `applications_app_samples` 的实时 revision，只读统计允许
   范围内全部 `.ets/.ts` 的 component/API/decorator/call/import-use/symbol 等事实。
2. 生成现有 24 个 Active Tag 的覆盖/漏覆盖表和候选 Tag 清单；每个候选必须列出真实代码样本、
   应用/family 多样性、trigger、Dimension/RQ、hard negative 和 Parser/owner 风险。
3. 候选发现不自动修改 `tags.yaml`。一次只评审一个候选，先冻结人工 Truth，再 shadow 运行并
   量化 exact/routing Precision/Recall，之后才决定 Reject、Draft 或 Active。
4. 独立推进真实 Pair Truth campaign 和 near-duplicate policy 校准；它用于保护后续 blind
   campaign，不阻塞当前的只读候选发现。
5. Tag 质量门禁建立后，再扩充真实 Context/Retrieval/Rule/Final Finding Truth；不得反向改变
   `ContextPlanResult`、`FeatureRoutingResult` 或人工 expected。
