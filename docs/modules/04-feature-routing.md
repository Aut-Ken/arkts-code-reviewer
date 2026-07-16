---
title: 04 Tags、评审维度与问题路由模块
status: canonical
implementation: complete
updated: 2026-07-15
---

# 04 Tags、评审维度与问题路由模块

## 1. 模块职责

Feature Routing 把 occurrence-scoped 代码事实转换为稳定、可重放的评审策略：

```text
FileAnalysis / UnitFactScope
-> FeatureRouter(tags-v1 + dimensions-v1)
-> UnitFeatureProfile / FeatureRoutingResult
-> ReviewQuestionBinding
-> ContextPlanner
```

它回答三个问题：

1. 当前 Unit 精确属于哪些代码场景。
2. 本次评审应检查哪些维度和问题。
3. 哪些文件级弱信号只允许保守扩大后续路由。

Tags、Dimensions 和 Review Questions 都不是代码问题，也不是 Finding evidence。Feature
Routing 不检索知识、不运行 Rules、不拼 Prompt，也不判断代码是否违规。

## 2. 概念区别

| 层 | 回答的问题 | 示例 |
|---|---|---|
| Fact | 代码里出现了什么 | API occurrence `setInterval` |
| Tag | Unit 属于什么场景 | `has_timer` |
| Dimension | 从什么方向检查 | `DIM-06` 资源与内存管理 |
| Review Question | 这次应回答什么问题 | `RQ-resource` |
| Evidence | 规范如何要求 | 定时器应在不再使用时清理 |
| Finding | 当前代码是否有问题 | 定时器创建后没有释放 |

前三层只做适用性和路由。后两层分别属于 Retrieval/Rules 和最终评审。

## 3. 当前实现与调用链

当前主实现位于：

| 文件 | 当前职责 |
|---|---|
| `feature_routing/config.py` | YAML schema、交叉引用、配置 fingerprint 和 fail-closed loader |
| `feature_routing/matcher.py` | Facts 到 Tag signal 的确定性匹配 |
| `feature_routing/engine.py` | `FeatureRouter`、Dimension policy 和 Review Question 选择 |
| `feature_routing/models.py` | `UnitFeatureProfile/FeatureRoutingResult` 及图重放校验 |
| `config/tags.yaml` | `tags-v1`：24 个 Tag 及触发器 |
| `config/dimensions.yaml` | `dimensions-v1`：12 个 Dimension 和 12 个 Review Question |
| `code_analysis/tagger.py` | 旧 `derive_tags/trigger_dimensions` 的配置驱动兼容包装 |
| `code_analysis/analyzer.py` | 组装正式 Feature 产物和兼容 `RetrievalQuery` 视图 |

Loader 和 `FeatureRouter(config=...)` 还支持显式 `tag-config-v2/v3/v4`。v1/v2
schema 语义已冻结：v2 只增加 `any_import_use`；v3 的纯 leaf 匹配保留为 FR-02
development regression；v4 增加 per-symbol owner-role 约束，只用于 FR-02B
owner-aware shadow candidate。仓库默认配置、wheel defaults 和 `CodeAnalyzer` 仍运行
`tag-config-v1/tags-v1`。

真实生产链为：

```text
完整源码 parse-once
-> FileAnalysis
-> ReviewUnit owner/span 投影
-> UnitFactScope(unit_exact, file_hints)
-> FeatureRouter.route(all UnitFactScope)
-> UnitFeatureProfile[]
-> FeatureRoutingResult
   ├── question_bindings -> ContextPlanner
   └── tags/dimension routes -> 后续 Retrieval/Rules/Prompt
```

`AnalysisResult.validate()` 会使用相同 UnitFactScopes 和生效配置重放整个
`FeatureRoutingResult`，并检查旧 `RetrievalUnit` 兼容视图与正式 profile 一致。调用方不能
伪造 Tag、Dimension、MR 并集或 QuestionBinding 后仍通过结果校验。

FR-02B 不改动上述默认入口。显式 v4 影子评估必须把同 source revision 的三个正式对象绑定为
`OwnerAwareRoutingInput(scope, unit, file_analysis)`，再调用
`FeatureRouter(v4_config).route_owner_aware_shadow(inputs)`。这使 owner-role evidence 可由
FileAnalysis 重放，同时避免把 FileAnalysis 偷塞进默认 `route(scopes)` 或修改
`CodeAnalyzer` 生产链。对 v4 config 调用旧 `route(scopes)` 会直接报 contract error，不能
静默产出缺少 owner input 的半成品结果。

## 4. `unit_exact` 与 `file_hints`

每个 Unit 的唯一事实入口是 RU-3 产出的 `UnitFactScope`：

```text
unit_exact
  owner 为当前 Unit 或其后代
  occurrence 完整落在 Unit source span
  质量为 exact 或 recovered

file_hints
  同一个不可变 source_ref_id 的文件级存在信号
  不能声称属于当前 Unit
```

硬边界：

- `exact_tags` 只从 `unit_exact` 生成。
- `routing_tags` 只从当前 source 的 `file_hints` 生成。
- `any_import_use` 只在 `unit_exact` scope 求值；`file_hints` 永远不能通过该 operator 生成
  routing Tag。
- 一个 Unit 的 exact Tags 不会传播给同文件或其他文件的 Unit。
- 同文件 Unit 可以看到相同 routing Tags，但必须保留 `file_hint` scope。
- hint-only signal 不能生成 Unit 精确 Dimension、专项 Review Question 或 Finding evidence。
- `TagMatch` 保存 `tag_id/status/scope/signals`，每个 signal 记录 fact kind 和 value。
- `feature-routing-v1` signal 严格只有 `kind/value`；`feature-routing-v2` 的归一化 signal
  可原子增加 `operator/normalized_value`，operator 为 `any_symbol_leaf`。
- `feature-routing-v3` 的 exact signal 使用
  `operator=any_unit_symbol_leaf_with_owner_role`，除 raw symbol 和 leaf 外，必须逐 symbol
  保留 `owner_role`、symbol occurrence、direct/enclosing owner declaration 和 role evidence
  occurrence IDs；不能借用同文件 sibling owner 的 decorator。
- Method Unit 只绑定自身 method declaration；struct Unit 只绑定该 struct 的直接 lifecycle
  method 子声明。嵌套 ordinary class 的同名 method 必须 abstain，不能继承外层 ArkUI role。
- `any_unit_symbol_leaf_with_owner_role` 只在 `unit_exact` 求值；routing-only
  `any_file_symbol_leaf` 只在 `file_hint` 求值，二者必须在 trace 中保留各自 operator。
- fallback 或 owner 未解析时，exact facts 可以为空；不得按 span 猜测 owner。

当前 `UnitFactScope` 只携带 `unit_owner_unresolved` 这一 Unit 级 diagnostic，不携带完整
`FileParserQuality`。Parser layer、ERROR/missing node 和文件 warning 仍从 `FileAnalysis`、
`AnalysisMetadata` 与 ReviewUnit diagnostics 获取，不能从 Feature profile 是否有 Tag 推断。

## 5. 当前 24 Tags

| 类别 | Tags |
|---|---|
| 资源 | `has_image`, `has_timer`, `has_subscription`, `has_media`, `has_file_io` |
| 并发 | `has_async`, `has_taskpool`, `has_worker` |
| UI/体验 | `has_interactive_component`, `has_layout`, `has_responsive_api`, `has_text_display`, `has_resource_ref` |
| 安全/数据 | `has_permission_request`, `has_user_input`, `has_network`, `has_storage` |
| ArkTS/ArkUI | `has_state_management`, `has_lifecycle`, `has_list_render`, `has_animation`, `has_builder`, `has_navigation`, `has_logging` |

精确触发条件以 `config/tags.yaml` 为唯一运行时真值。当前 v1 已特别冻结：

- `clearInterval/clearTimeout` 与创建 API 一样属于 `has_timer`。
- subscription 只接受配置登记的 `emitter/sensor` API，不接受任意 `*.on`。
- `onAppear/onError` 不单独触发交互 Tag；`onClick/onTouch/onFocus/onBlur/onChange` 是受控信号。
- `resource_references` 可以直接触发 `has_resource_ref`，不要求伪造 `$r` API。

`tag-config-v2` 已冻结 owner-aware `any_import_use` operator，并明确拒绝
`any_symbol_leaf`。`tag-config-v3` 的 `any_symbol_leaf` 只保留为 FR-02 development
regression；它无法区分普通 class 与 ArkUI owner，不是待激活的生产候选。

FR-02B fixture `tests/fixtures/feature_routing/tag_config_lifecycle_owner_role_shadow_v1.yaml`
声明 `tag-config-v4/tags-lifecycle-owner-role-shadow-v1`，并使用 exact-only
`any_unit_symbol_leaf_with_owner_role`。每个配置项为 `{symbol_leaf, owner_role}`，当前映射为：

| Lifecycle leaf | 允许的 owner role |
|---|---|
| `aboutToAppear`, `aboutToDisappear` | `arkui_custom_component` |
| `onBackPress`, `onPageHide`, `onPageShow` | `arkui_router_page` |

`onReady` 明确从 owner-aware exact 映射排除：`Canvas.onReady` 是组件事件回调，不作为
page/custom-component lifecycle；它只保留在 `any_file_symbol_leaf` 中作为 routing hint。
Owner role 在 Feature Routing 边界由既有 FileAnalysis declaration、decorator、
owner 和 occurrence identity 派生；Parser schema 与 Parser v1 行为不变。若证据缺失、
recovered、冲突或无法唯一绑定到当前 symbol，owner-aware exact matcher 必须 abstain。
`arkui_custom_component` 只接受同一 enclosing struct 的 `@Component/@ComponentV2`
evidence；`@CustomDialog` 暂不映射 owner role，必须 abstain。`arkui_router_page` 还需要该
struct 的 `@Entry` evidence。
该约束同时覆盖 method Unit 与 struct Unit 的直接 lifecycle 子声明，不把 struct 内嵌套
ordinary class 的同名方法提升为 lifecycle。

V4 的 `any_file_symbol_leaf` 只保留同文件 weak routing signal。它可以生成 routing Tag
和保守 routing/MR Dimension，但不能声明当前 Unit 的 owner role，不能绑定专项 RQ，
也不能成为 Finding evidence。V3/v4 都不会改变默认 24 个 Tag、Dimension、Review
Question 或 v1 Golden；candidate 必须再经过独立 blind holdout 与真实 P/R 门禁，才能讨论
正式配置迁移。

独立 holdout 的代码合同现已实现，但真实证据尚未产生。该链单独使用
`lifecycle-holdout-selection-v1`、`lifecycle-holdout-review-packet-v1`、两份
`lifecycle-holdout-review-receipt-v1` 和 `lifecycle-holdout-consensus-v1`；必须先由外部独立
人员完成无标签选样和双审共识并提交 seal，post-seal evaluator 才能首次运行 v4 candidate。
候选设计已看过 `applications_app_samples@8255a2987f70317cc3a2a4d46044c6b55f092bb3`
整棵 tracked tree；该 revision 不能作为独立样本，未来 selection 必须来自严格后继 descendant，
且相对 exposure tree 和 canonical development Truth 都满足全新/排除的 family、path、content
边界。

V1 policy 固定为 32 case/32 family 的 `purposive_stratified_challenge_holdout`：component v1/v2
正例各 4、router 正例 8、nested/non-entry/ordinary/routing-only 四类 negative 各 4，不声明
随机抽样、inclusion probability 或 natural prevalence。当前 eligible corpus 没有独立
non-DocsSample `@ComponentV2` family，所以真实 selection 尚不可构造，不能通过缩减 stratum 或
复用 development case 绕过。

Selection 同时冻结 candidate commit 的完整 Python tree、tags/dimensions、Parser sidecar、
candidate config、Python/Node/`node_modules` environment 和 evaluation harness。Packet 只能从
canonical contract/policy、selection 与 verified checkout 重建；Parser、ReviewUnit、
UnitFactScope、owner/file-hint/routing-only safety counts 均要求为 0。即使未来报告得到
`evidence_ready=true`，也只表示迁移证据通过，不会自动切换默认 `tags-v1`。流程、工具与当前缺口见
`tests/evaluation/lifecycle_blind_holdout_v1/README.md`。

为避免每个 Tag 继续复制一套互不兼容的真实样本 schema，EVAL-01A Stage 1 另建通用
`tag-contract-snapshot-v1 + tag-truth-v2` 数据合同。它只描述一个 immutable ReviewUnit 对一个
目标 Tag 的 exact applicability 与 routing-hint applicability 两条独立判断轴；每条轴分别冻结
positive、negative 与 abstain/taxonomy decision，明确
不包含 candidate trigger、prediction、behavior baseline、Dimension/RQ、Knowledge、Retrieval 或
Finding truth。真实数据角色严格区分 `development_regression`、
`independent_blind_challenge` 和 `production_prevalence`；旧 48-case 与 RelationalStore v1 只能由
coverage adapter 报告为 development/provisional，不能自动升级为独立证据。
Stage 1 loader 也会拒绝自称 blind/prevalence 的 manifest，直到后续存在外部 selector、seal 与
版本化 verifier。

EVAL-01B Stage 2A 只向前补齐 generic unlabeled selection verifier、policy-sized structural
selection-capacity lower-bound assessment 和 dual-axis review packet infrastructure，不产生真实 selection、packet、label、
receipt、consensus 或 candidate result。候选团队已经看过
`applications_app_samples@8255a2987f70317cc3a2a4d46044c6b55f092bb3` 的完整 tracked tree，
因此同一 revision 不能被重抽样后宣称为 blind。当前本地也没有已登记、可验证的
strict descendant；真实 blind selection 因此是 `not_constructible`，质量证据仍是
`not_qualified`。未来必须先登记符合排除边界的 strict-descendant revision，再由独立
dataset custodian 在候选开发流程外生成和封存无标签 selection。

容量下界只计 regular Git file、safe path、非空 UTF-8、unique content 和保守的互不包含
family 集合；它允许少报但不允许因重复 content 或嵌套 family 误报。即使该下界达到 policy
的总 case 数，报告也只允许写 `inventory_capacity_only`；各 proxy
stratum 的容量仍是 `not_measured`，不能宣称完整 selection 已可构造。Stage 2A 中的 proxy
stratum/rank 只用于控制挑战样本的多样性，不是 Tag Truth；
即使 proxy 来自 import、call 或 symbol 也不能自动成为 positive/negative。通用 packet
为了同时审核 exact 和 routing-hint 两条轴，展示隐去仓库路径的整文件正文，由 reviewer 自己确定
ReviewUnit；它隐藏 path、family、proxy stratum、selection rank 和 candidate 身份、配置、
输出与诊断，也不向 reviewer 暴露 repository revision 或原始 source hash。这个设计防止
selector 预选的 span 或 proxy 类别变成人工 Truth；它是 candidate-blind/path-redacted，
不是匿名化保证，源码内部标识符仍可能暴露来源。
本阶段不修改 Matcher、默认 Tag/Dimension/RQ 或任何组合 fingerprint。

EVAL-01B Stage 2B 在上述 packet 后增加通用双人审核基础设施：
`tag-truth-v2-review-receipt-v1` 把 self-hashed packet、其中记录的 `selection_id`、Tag contract、
完整 review policy fingerprint 与一名 human reviewer 对全部 case 的 ReviewUnit/exact/routing
决策绑定；
`tag-truth-v2-consensus-v1` 只接受同一 packet 的两份不同 reviewer receipt，并保留两票原始
rationale/evidence。ReviewUnit identity 不一致时该 case 的 exact/routing 两轴都 unresolved；
Unit 一致后两轴独立形成共识，一轴分歧或 abstain 不得删除另一轴已经一致的 judgement。
双方一致要求 taxonomy decision 时输出 `agreed_abstain` blocker，不得改写成 negative。

`tools/seal_tag_truth_v2_review_receipt.py` 对合法 receipt 返回 0、非法输入返回 2；
`tools/build_tag_truth_v2_consensus.py` 对无 unresolved/abstain 的完整共识返回 0，对合法但含
unresolved 或 `agreed_abstain` 的共识返回 1，对非法 schema/binding/coverage/reviewer 输入返回 2。
两者都不加载或运行 candidate。`consensus_status=complete` 只证明两份有效人工 receipt 对所有
axis 已形成共识，不表示 evidence qualified、candidate qualified 或 activation ready。

Stage 2B 没有生成真实 selection、packet、receipt 或 consensus；当前仍没有合格的
strict-descendant source revision，selection/review policy 仍未批准，near-duplicate qualification
和 sealed first candidate run 也不存在，所以真实证据继续是 `not_qualified`。本阶段不修改
Matcher、默认 Tag/Dimension/RQ、Parser、Golden 或任何组合 fingerprint。后续还需分别实现
consensus 到 `TagTruthV2Suite` 的 publication bridge、版本化 near-duplicate 判定、真实外部
policy/selection 与 seal、post-seal first-run runner 和质量门禁，不能从 receipt/consensus
schema 直接跳到配置激活。

Stage-2B CLI 只验证 packet 自哈希和其中记录的 `selection_id`，不接收外部 Stage-2A selection
artifact，也不在本阶段重新验证 selection/checkout provenance；该桥接属于上述后续工作。

EVAL-01B Stage 2C 增加 `tag-truth-v2-provenance-verification-v1` 和
`tools/verify_tag_truth_v2_git_seal.py`，把 Selection、Packet、两份 Receipt 与 Consensus 作为
恰好五份 committed artifact 重新桥接。正式验证必须在专用 checkout 中指定完整 seal commit，
要求 `HEAD == seal_revision`、worktree clean、五条路径唯一且都是仓内 regular non-symlink Git
blob，并逐字节比较当前文件与 `git show seal:path`。Candidate commit 必须是 seal 的严格祖先；
Git replace objects 会被禁用、ancestry 检查会关闭本地 commit-graph cache，project/source Git
common directory 中出现 legacy grafts 时会 fail closed；
随后 verifier 才从已捕获的 committed bytes 解析 artifact，重新验证 source checkout、exposure、
development exclusions、Packet、Receipt 和 Consensus。

这里的 seal 证明五份 artifact 同时存在并绑定在指定 seal tree，不证明它们恰好由该 commit
一起引入，也不限制该 commit 的其他 diff。Preflight 还会逐字节冻结 typed verifier closure，
拒绝 bytecode cache、symlink 和 import shadow；typed import 前会移除仓库内未封存的 Python
搜索路径。CLI 强制使用 Python isolated mode (`-I`) 隔离脚本目录、当前目录和 `PYTHONPATH`；
Python startup、解释器、标准库与 site-packages 仍属于 host trust boundary，不由本阶段冻结。

CLI 在任何项目模块 import 前先运行纯标准库 preflight，并且从不运行 Parser、Matcher、
FeatureRouter 或 candidate。完整 consensus 的合法 provenance 返回 0；合法但 unresolved/abstain
的封存链返回 1；schema、Git、路径、checkout 或 binding 失败返回 2。输出 report 是 seal 后的
可重建审计结果，不属于同一个 seal commit，避免 commit identity 自引用。即使验证成功，report
也固定表示 evidence `not_qualified`、candidate `not_run`；Git/self-hash 只证明受检字节和关系，
不认证 selector/reviewer/remote/runner，也不能证明“首次运行”。

当前仍没有真实五产物 seal，Stage 2C 也不实现 near-duplicate、`TagTruthV2Suite` publication、
candidate runtime/environment freeze、P/R、质量门禁或激活；这些继续作为独立后续问题。

EVAL-01B Stage 2D1 在 Stage 2C 之后增加版本化 near-duplicate **shadow screening**，但不把
它包装成正式 qualification。筛查器必须先重建并核对 Stage-2C provenance，再从两位 reviewer
一致选择的 ReviewUnit span 提取 Unit 正文；它分别比较 ReviewUnit 轴和整文件轴，reference
覆盖 candidate commit 的项目 tracked text、完整 exposure tree、development Truth 在原 revision
登记的 source，以及同一 campaign 的其他 blind case。扫描只读取 pinned Git blob，不读取或运行
Parser、Matcher、FeatureRouter 或 candidate。

`lexical-content-v1` 会忽略空白/注释、保留 identifier/keyword/operator 并归一化 literal；
`lexical-shape-v1` 进一步抽象普通 identifier，且只能把结果送入灰区，不能单独 hard reject。
固定的 7-token content shingle、11-token shape shingle、双向 containment、Jaccard、最长连续
token run 和 content/shape normalized-token equality 都以确定性整数比较执行；shape 只能触发
灰区，content 灰区还必须至少共享 16 个 shingle。比较前冻结 NFC 字符工作量：16M selected、
64M unique reference、2M pair、250M pair-side characters 和 10k recorded match 任一超限，都会
整批丢弃 partial 结果并 abstain。短 Unit、灰区、reference inventory 不完整、tokenization/
resource failure 或 unresolved consensus 必须显式 `review_required/abstain`，不得解释为“没有
重复”。`probe_evaluation_status=not_run_resource_limit` 下的 0 token/shingle 只是未运行标记。

Stage 2D1 的 policy、screening core、preflight 和 CLI 都必须在 candidate/seal 保持同一 Git blob；
policy 仍是 `snapshot_only_not_approved` 校准快照，当前阈值只是后续人工 pair Truth 校准的 seed。
因此合法的
shadow clear、duplicate 或灰区报告都仍返回 1，并固定记录 near-duplicate/evidence
`not_qualified`、candidate `not_run`；返回 0 保留给未来另行批准和校准的 policy，非法 schema、
Git、path、binding 或 rebuild 返回 2。当前没有真实 Stage-2D1 campaign，也没有独立 duplicate /
independent / ambiguous pair Truth。该基础设施不修改 Tag/Dimension/RQ、Matcher、Parser、Golden、
`TagTruthV2Suite` 或 Feature Routing fingerprint。
Report self-hash 只绑定 identity；正式消费必须以 sealed policy/inventory/五件套完整 rebuild。
当前 `fdac0fc` 项目 tree 已知同时存在 24,144,840-byte oversize generated blob、54 个 all-text
ArkTS-like tokenizer issue，且 48-probe 估算只算项目 reference 就超过 727M pair NFC chars，因而
真实运行会保守 abstain，不能宣称 near-duplicate 已 qualified。

EVAL-01B Stage 2D2a 在此后增加独立的 consensus publication bridge。它完整重建 Stage 2C/2D1
后，只允许两种输出：complete consensus 且所有 file/Unit screen 均 clear 时生成
`published_consensus_not_qualified`；disagreement、taxonomy abstain、duplicate、gray 或任何
screening/inventory/resource blocker 则生成 `blocked_no_suite`，suite 必须为空。发布内容无损保留
exact/routing 两轴、agreed ReviewUnit 和两名 reviewer 的完整原始 vote，包括各自 evidence line
与 rationale；suite 通过自哈希 `chain_binding_id` 绑定五件套、seal、candidate/source/exposure
tree、Feature config、Stage-2C/2D1 ID 和 inventory fingerprint。

该 schema 不替换或放宽 Stage 1 `TagTruthV2Suite`，不从 proxy stratum 猜正负标签，也不补造
critical-negative、normalized-body、template-cluster 或 quality gate。它不运行 candidate、不计算
P/R、不修改 Tag/Dimension/RQ、Matcher、Parser、Golden 或 Feature Routing fingerprint。即使
publication CLI 返回 0，也只表示共识 Truth 投影成功；evidence 仍是 `not_qualified`，candidate
仍是 `not_run`，quality gate/activation 仍是 `not_evaluated`。两种 publication 状态都携带同一
readiness envelope；单独 parse self-hash 或 suite fingerprint 不能替代 full verifier。

只读 `tools/report_tag_truth_coverage.py` 会把当前 24 个正式 Tag 的 synthetic exact/routing
正例、真实 development 正/负例、family、review status、Parser-risk availability 以及
blind/prevalence 缺口分栏输出。缺失数据必须显示 `not_measured` 或 `not_qualified`，不能用 0 或
整体 micro 指标掩盖。Stage 1 不迁移旧 manifest、不运行 candidate、不实现通用 blind selector，
也不改变默认 Tag/Dimension/RQ 或组合 fingerprint；完整边界见
`tests/evaluation/tag_truth_v2/README.md`。

新增或修改触发器必须升级 Git 中的配置、重跑 Golden，并记录新的组合 fingerprint；外部文档、
Skills 或代码语料不能在运行时自行创建 Tag。

## 6. 当前 12 Dimensions 与四种集合

| ID | 名称 | v1 review policy |
|---|---|---|
| DIM-01 | 规范符合度 | always check，retrieval disabled |
| DIM-02 | ArkTS 语言特性 | always check，signal required |
| DIM-03 | 性能 | always check，signal required |
| DIM-04 | 可维护性 | always check，signal required |
| DIM-05 | 健壮性 | always check，signal required |
| DIM-06 | 资源与内存管理 | resource Tags |
| DIM-07 | 并发与异步 | async/taskpool/worker Tags |
| DIM-08 | 无障碍 | interactive Tag |
| DIM-09 | 多设备适配 | layout/responsive Tags |
| DIM-10 | 国际化 | text/resource Tags |
| DIM-11 | 安全 | permission/input/network/storage Tags |
| DIM-12 | DFX 与可测性 | always check，signal required |

`UnitFeatureProfile` 不把所有 Dimension 混成一个列表：

| 字段 | 语义 |
|---|---|
| `dimensions` | 本 Unit 实际需要评审：`always_check` 或有 exact Tag |
| `always_check_dimensions` | 配置要求每个 Unit 都检查的方向 |
| `retrieval_dimensions` | 满足 retrieval policy 且有 exact signal 的正式检索维度 |
| `routing_dimensions` | exact 或 hint signal 支持的保守检索候选维度 |
| `shadow_dimensions` | Draft 配置的影子结果，不进入正式执行 |
| `mr_dimensions` | 所有 Unit `dimensions + routing_dimensions` 的稳定并集 |

每个 Active Dimension 都有一个 `DimensionRoute`，记录 `always_check`、
`retrieval_policy`、`review_enabled/retrieval_enabled/routing_enabled`、signal scope 和命中的 exact/
routing Tags。规则为：

```text
review_enabled
  always_check OR exact tag matched

retrieval_enabled
  policy=always OR (policy=signal_required AND exact tag matched)

routing_enabled
  policy=always OR (policy=signal_required AND exact/hint tag matched)
```

因此 hint-only signal 只能进入 `routing_dimensions`，不能进入 `retrieval_dimensions`。
DIM-02/03/04/05/12 当前虽 `always_check=true`，但其 `signal_required` trigger 列表为空，所以
v1 会检查这些方向，却不会据此发起知识检索。为这些抽象维度补静态信号属于后续配置版本，
不能把“始终检查”偷换成“始终检索”。

## 7. 当前 12 Review Questions

`dimensions-v1` 同时冻结 12 个问题：

```text
RQ-correctness             始终绑定
RQ-accessibility           has_interactive_component
RQ-adaptability            has_layout / has_responsive_api
RQ-concurrency             has_async / has_taskpool / has_worker
RQ-dfx                     has_logging
RQ-internationalization    has_resource_ref / has_text_display
RQ-lifecycle               has_lifecycle
RQ-navigation              has_navigation
RQ-network                 has_network
RQ-resource                resource Tags
RQ-security                permission/input/network/storage Tags
RQ-state                   has_state_management
```

Active 专项问题只消费 `exact_tags`；hint-only signal 不绑定专项 RQ。Draft 问题进入
`shadow_review_question_ids`，Deprecated 问题不进入输出。

职责边界必须保持：

- Feature Routing 拥有问题 registry 和“哪些 Primary 适用哪些问题”的选择。
- ReviewUnit 拥有 `QuestionBinding` 的承载形状、ChangeGroup、按问题分 bundle 和预算语义。
- `CodeAnalyzer.plan_context(...)` 把 `FeatureRoutingResult.question_bindings` 转换为 ContextPlanner
  的既有 `QuestionBinding`；兼容参数若显式提供，只能作为相等性断言，不能覆盖路由结果。
- Retrieval、Rules 和 Prompt 只能消费问题选择，不能反向修改 Primary 的适用性。

## 8. 版本化配置与 fingerprint

当前运行时配置已经落盘：

```text
config/tags.yaml             tag-config-v1 / tags-v1
config/dimensions.yaml       dimension-config-v1 / dimensions-v1
```

显式注入配置遵循以下版本边界：

| Tag config schema | 新增 operator | Feature output | 当前角色 |
|---|---|---|---|
| `tag-config-v1` | 无 | `feature-routing-v1` | 默认生产配置，冻结 |
| `tag-config-v2` | `any_import_use` | `feature-routing-v1` | 已冻结；仅显式注入 |
| `tag-config-v3` | `any_symbol_leaf` | `feature-routing-v2` | FR-02 development regression |
| `tag-config-v4` | `any_unit_symbol_leaf_with_owner_role`, `any_file_symbol_leaf` | `feature-routing-v3` | FR-02B candidate-only |

schema version 和 Tag config 内容版本不是同一概念。支持 `tag-config-v4` 不表示默认
配置已升级，也不表示 candidate 已激活。

Loader 使用 `ruamel.yaml` safe mode 拒绝重复 key，再由 Pydantic strict model 校验：

- `extra=forbid`，未知字段直接失败。
- ID、版本和文本必须非空且满足固定格式。
- trigger 数组必须去重、升序且非空语义合法。
- Tag、Dimension、Question ID 不得重复。
- 引用的 Tag 必须存在；Active Dimension/Question 不得依赖非 Active Tag。
- 未知 status、retrieval policy 或 trigger operator 不得静默忽略。

`FeatureConfig.fingerprint` 对排序规范化后的 tags、dimensions、questions 和两个声明版本做
SHA-256，输出格式为 `feature-config:sha256:...`。每个 profile 都携带该值；顶层 result
另外携带实际 Tag/Dimension config version，默认值是 `tags-v1/dimensions-v1`。配置内容或
版本变化会改变所有相关 identity。

v1 fingerprint 和通用 Tag 序列化显式排除后续 schema 的默认字段，保证仅增加 loader 能力
不会造成既有 Feature 或 Knowledge 身份漂移；v2 fingerprint 包含 `any_import_use` 的
规范化值，v3 fingerprint 包含纯 leaf trigger，v4 fingerprint 还必须包含 leaf-to-role
映射和 routing-only file leaf 集合。v1/v2 输出 `feature-routing-v1`，v3 输出 `feature-routing-v2`，v4 输出
`feature-routing-v3`。

源码运行时优先读取仓库 `config/`；wheel 构建通过 `pyproject.toml` 的 `force-include` 把两份
YAML 安装到 `arkts_code_reviewer/feature_routing/defaults/`，安装环境优先读取 packaged defaults。
因此 editable/source checkout 和 wheel 使用同一份受版本控制的语义，不依赖调用者当前目录。

## 9. Active、Draft、Deprecated

治理状态不只是文档标签：

```text
Active      进入正式 exact/routing Tags、Dimensions、Questions
Draft       只进入 shadow_*，不影响正式路由和 ContextPlanner binding
Deprecated  保留配置历史语义，但运行时不匹配、不输出
```

Active Dimension 或 Question 不能引用 Draft/Deprecated Tag。删除既有 ID 会破坏历史报告，应先
Deprecated 并通过新配置版本迁移；不能直接物理删除后复用同一个 ID。

## 10. 稳定性与 fail-closed 重放

`UnitFeatureProfile` 和 `FeatureRoutingResult` 是不可变、稳定排序的正式产物：

- `profile_id` 和 `feature_routing_id` 由完整语义字段确定性计算。
- Tag activation trace 必须与 exact/routing/shadow Tag 集合完全一致。
- Dimension 集合必须能从 `dimension_routes` 完整重放。
- Question bindings 必须严格等于各 profile 的 Active question IDs。
- `mr_dimensions` 必须严格等于 Unit review/routing Dimension 并集。
- Unit、TagMatch、DimensionRoute、QuestionBinding 和所有 ID 列表均排序去重。
- `validate_replay(scopes, config)` 必须得到与当前结果完全相同的对象。
- `feature-routing-v3` 必须改用
  `validate_owner_aware_replay(OwnerAwareRoutingInput[], config)`；通用 replay 会拒绝 v3，
  防止只给 UnitFactScope 而遗漏 ReviewUnit/FileAnalysis owner evidence。

模型构造器只证明图内部一致；默认 v1 由 `AnalysisResult.validate()` 和显式
`validate_replay(scopes, config)` 证明结果来自指定 facts/config，v3 则必须使用上面的
owner-aware replay。来自存储或网络的独立 artifact 未重放前不应被信任。

## 11. 兼容输出边界

旧 `CodeFeatures`、`RetrievalUnit`、`RetrievalQuery/MrContext` 仍保留，供现有 CLI 和测试迁移。
它们现在必须与正式 `FeatureRoutingResult` 对齐，但仍是 compatibility-only 视图：

- `RetrievalUnit.code_features.tags == UnitFeatureProfile.exact_tags`
- `RetrievalUnit.dimensions == UnitFeatureProfile.dimensions`
- `RetrievalUnit.routing_tags == UnitFeatureProfile.routing_tags`
- `MrContext.triggered_dimensions == FeatureRoutingResult.mr_dimensions`

后续 Retrieval 不得直接读取旧 `RetrievalQuery.dimensions` 或 MR 并集后自行决定检索，否则会
绕过 `retrieval_policy`、exact/hint scope、Draft 隔离和 QuestionBinding。正式入口必须消费
`UnitFeatureProfile.retrieval_dimensions/routing_dimensions` 及 activation trace。

## 12. Feature Routing Golden

独立 Golden 位于 `tests/golden/feature_routing/`：

- 16 个自包含、hash-pinned、人工 expected case。
- 覆盖全部 24 Tags、12 Dimensions、12 Review Questions、20 Unit profiles、69 种 scoped
  signal（共 86 个 Unit-level occurrence）和 44 question bindings，以及 exact/hint 隔离、跨文件隔离、fallback、
  timer cleanup、subscription/interaction 反例、资源 occurrence 和输入排列稳定性。
- expected 与 `baselines/current.json` 分离；baseline 不能生成或覆盖 expected。
- loader 拒绝重复 key/case/identity、未知或缺失字段、未排序列表、非法 Tag/Dimension/RQ、
  activation signal、配置版本或 question binding 漂移、source hash/ref 漂移、路径逃逸和 symlink。
- evaluator 使用正式 `FeatureRouter`，当前 strict baseline 与 `--require-perfect` 均为 `16/16`。

当前指标为：exact/routing Tag、review/retrieval/routing/MR Dimension、Review Question、
activation signal、question binding 的 precision/recall，以及 case exact accuracy 和
input-order stability 均为 `1.0`。这表示冻结 16-case truth 全匹配，不等于任意
ArkTS 仓库总体准确率为 100%。新增 trigger 或真实场景必须扩充分母，而不是只更新 baseline。

`any_import_use` 的合同由 config、matcher 和
Parser → FileAnalysis → ReviewUnit → UnitFactScope → FeatureRouter 跨层测试证明。默认
16-case Golden 仍只冻结 `tags-v1` 行为，不能据此声称新 Tag 的真实语料 Precision/Recall
已经合格。

现有 FR-0 Golden 继续严格冻结 `tags-v1 + feature-routing-v1`，不得为了候选结果
改写 expected 或 v1 signal shape。FR-02 的 `tag-config-v3 + feature-routing-v2` 只保留为
development regression；FR-02B 的 `tag-config-v4 + feature-routing-v3` 由单独的
Parser → ReviewUnit → owner-aware FeatureRouter 链路 E2E 和 candidate report 验证。

原 7 个 cross-target lifecycle additions 已记录人工正裁决（非 blind、非独立），属于 development
regression truth；`TR-TIMER-008` 仍是明确要求 lifecycle co-Tag 的 case contract。
当前全部 48 case 已参与规则设计和迭代，历史 `calibration/acceptance_holdout` 名称只表示
fixture split，不能再把其中任何一组解释为 blind holdout。独立 blind holdout 仍缺失，
因此即使 candidate contract 全部重放通过，也必须报告 `activation_ready=false`；contract
gate 不是总体 Precision/Recall 或生产激活门禁。

当前 pinned E2E 在 selected development-regression rows 上得到
`15 TP / 5 TN / 0 FP / 0 FN`，并得到
`declared_contract_gate.passed=true`；同一 report 的 `candidate_evidence_gate.passed=false` 与
`activation_ready=false` 仍由 provisional truth、development-regression-only 和缺少独立
adjudicated holdout 三项 blocker 共同保持。

发布包另运行：

```bash
PYTHONPATH=src .venv/bin/python tools/check_feature_routing_package.py
```

## 13. 当前明确限制

正式 Feature Routing v1 已完成；`feature-routing-v2` 是 development regression provenance，
`feature-routing-v3` 是 shadow candidate provenance，二者都不是生产升级。当前仍没有实现
以下下游或扩展能力：

- `ApiSymbolCatalog` builder 和版本/权限感知的 API taxonomy。
- relation/call graph discovery；caller、state access、lifecycle pair 等仍需显式关系来源。
- Knowledge Clause、在线 Retrieval、Rules、Prompt、LLM、Finding 和 GitCode。
- 抽象维度 DIM-02/03/04/05/12 的可检索静态信号。
- hint-only 专项 Review Question；v1 有意不绑定，避免把文件级信号冒充 Unit 适用性。
- Tag 级完整 Parser quality；默认 v1 profile 只能保留 `UnitFactScope` 的
  `unit_owner_unresolved` diagnostic，v3 虽增加 owner-context abstain/quality diagnostics，仍不等于
  完整 `FileParserQuality`。
- `CodeAnalyzer` 的运行时配置注入；v1 Analyzer 固定使用默认配置，显式自定义配置目前只支持
  直接 `FeatureRouter` 测试/影子运行。
- `any_import_use` 只能识别 Unit 涉及某个 canonical import identity；它不识别具体 call
  role、receiver 类型、wrapper/间接调用或运行时行为。这些属于后续 calls/provenance 合同，
  不得从 import-use 命中推断。
- `any_symbol_leaf` 只比较最后一个点分段，无法区分 ArkUI component/page 与普通
  class 的同名方法；FR-02B 通过 per-symbol owner-role evidence 解决该已知合同缺口，但尚未
  由独立 blind holdout 证明可激活。

这些限制必须由对应模块或新的配置/数据合同版本解决，不能在 FeatureRouter 内递归扫描仓库、
复制文件级事实或生成 Finding。

## 14. 后续消费顺序

Feature Routing 的当前交付边界已冻结为：

```text
FeatureRoutingResult
├── UnitFeatureProfile[]
├── MR conservative dimensions
└── ReviewQuestionBinding[]
```

后续应分别建立 relation discovery、Knowledge/Retrieval、Rules 和 Prompt/Final Review Golden。
它们可以使用本模块给出的适用性和路由，但不能反向改变 `unit_exact/file_hints`、Tag activation
trace、retrieval policy 或 QuestionBinding truth。
