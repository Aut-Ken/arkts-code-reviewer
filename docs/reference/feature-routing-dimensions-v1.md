# Dimension 推导方法与完整路由表（dimensions-v1）

> 当前配置真值：[`config/dimensions.yaml`](../../config/dimensions.yaml)  
> 当前运行时版本：`dimensions-v1`  
> 适用基线：`de1d4b4`  
> 本文是配置与算法快照；配置、canonical 文档和代码与本文冲突时，以它们为当前事实

## 1. Dimension 到底是什么

Dimension 是“应该从哪个方向检查这段代码”，不是代码事实，也不是问题结论。

```text
setTimeout / clearTimeout 事实
        ↓
has_timer 场景 Tag
        ↓
DIM-06 资源与内存管理
        ↓
检索器优先寻找资源创建、持有、清理相关知识
```

必须区分四个概念：

| 概念 | 回答的问题 | 示例 |
|---|---|---|
| Fact | 代码里客观出现了什么 | `setTimeout` |
| Tag | 这属于什么代码场景 | `has_timer` |
| Dimension | 应从什么方向检查 | DIM-06 资源与内存管理 |
| Review Question | 具体要问模型/规则什么 | RQ-resource：资源是否正确创建、持有和释放 |

Dimension 不是通过向量搜索“搜出来”的。它首先由 Tag 按配置确定，然后用于正式评审方向、
保守路由审计和 Retrieval；Context Planning 直接消费的是独立生成的 Review Question 绑定，
不是 Dimension 本身。

## 2. Unit Dimension 的生成算法

### 2.1 输入

每个 `UnitFeatureProfile` 有两类 Active Tag：

```text
exact_tags    当前 Unit 有 occurrence/owner/span 证据
routing_tags  只能证明同文件存在的 file hint
```

对每个 Active Dimension，运行时计算：

```text
exact_matches = Dimension.any_tag ∩ Unit.exact_tags

hint_matches =
  Dimension.any_tag ∩ Unit.routing_tags
  - exact_matches
```

减掉 exact 是为了同一 Tag 同时出现在两个 scope 时，不重复归因。

### 2.2 三个开关

```text
review_enabled =
  always_check
  OR exact_matches 非空

retrieval_enabled =
  retrieval_policy == always
  OR (
    retrieval_policy == signal_required
    AND exact_matches 非空
  )

routing_enabled =
  retrieval_policy == always
  OR (
    retrieval_policy == signal_required
    AND exact_matches/hint_matches 任一非空
  )
```

这意味着：

- exact Tag 可以启动正式评审、正式检索和保守路由；
- file hint 只能启动保守 routing Dimension；
- `always_check=true` 只保证“要检查”，不自动保证“要检索知识”；
- `retrieval_policy=disabled` 永不产生检索维度。

当前生产配置没有 `retrieval_policy=always` 的 Dimension。

### 2.3 `signal_scope`

| 值 | 含义 |
|---|---|
| `unit_exact` | 只有 exact Tag 命中 |
| `file_hint` | 只有 file-hint Tag 命中 |
| `mixed` | exact 和 hint 各有不同的触发 Tag |
| `none` | 没有触发 Tag |

### 2.4 输出的六个集合

| 字段 | 准确含义 |
|---|---|
| `dimensions` | 本 Unit 正式应评审的方向：always-check 加 exact Tag 命中的方向 |
| `always_check_dimensions` | 当前固定为 DIM-01～05、DIM-12 |
| `retrieval_dimensions` | exact Tag 真正启动的正式知识检索维度 |
| `routing_dimensions` | exact 或 file hint 支持的保守背景维度 |
| `shadow_dimensions` | Draft Dimension 的影子结果，不进入正式执行 |
| `mr_dimensions` | 所有 Unit 的 `dimensions + routing_dimensions` 并集，只作 MR 总览 |

模型强制：

```text
retrieval_dimensions ⊆ routing_dimensions
```

Retrieval 使用每个 Unit 自己的 `retrieval_dimensions/routing_dimensions`，不会拿 MR 并集替代
Unit 真值。

## 3. 12 个 Dimension 完整路由表

| ID | 名称 | always_check | retrieval_policy | 触发 Tags | 当前实际行为 |
|---|---|---:|---|---|---|
| DIM-01 | 规范符合度 | 是 | `disabled` | 无 | 每个 Unit 都评审；永不因该维度检索 |
| DIM-02 | ArkTS 语言特性 | 是 | `signal_required` | 无 | 每个 Unit 都评审；当前没有触发 Tag，所以不产生正式/保守检索维度 |
| DIM-03 | 性能 | 是 | `signal_required` | 无 | 同上 |
| DIM-04 | 可维护性 | 是 | `signal_required` | 无 | 同上 |
| DIM-05 | 健壮性 | 是 | `signal_required` | 无 | 同上 |
| DIM-06 | 资源与内存管理 | 否 | `signal_required` | `has_file_io`、`has_image`、`has_media`、`has_subscription`、`has_timer` | exact 启动评审与检索；hint 只启动保守路由 |
| DIM-07 | 并发与异步 | 否 | `signal_required` | `has_async`、`has_taskpool`、`has_worker` | 同上 |
| DIM-08 | 无障碍 | 否 | `signal_required` | `has_interactive_component` | 同上 |
| DIM-09 | 多设备适配 | 否 | `signal_required` | `has_layout`、`has_responsive_api` | 同上 |
| DIM-10 | 国际化 | 否 | `signal_required` | `has_resource_ref`、`has_text_display` | 同上 |
| DIM-11 | 安全 | 否 | `signal_required` | `has_network`、`has_permission_request`、`has_storage`、`has_user_input` | 同上 |
| DIM-12 | DFX 与可测性 | 是 | `signal_required` | 无 | 每个 Unit 都评审；当前不产生维度检索 |

## 4. 完整 Tag → Dimension → Review Question 路由表

Dimension 与 Review Question 没有直接外键；它们分别根据 exact Tag 匹配。下表只是把两条
独立路由并排展示。

| Tag | Dimension | 专项 Review Question | 分类 |
|---|---|---|---|
| `has_animation` | 无 | 无 | taxonomy-only，待产品确认 |
| `has_async` | DIM-07 | RQ-concurrency | dimension-backed |
| `has_builder` | 无 | 无 | taxonomy-only，待产品确认 |
| `has_file_io` | DIM-06 | RQ-resource | dimension-backed |
| `has_image` | DIM-06 | RQ-resource | dimension-backed |
| `has_interactive_component` | DIM-08 | RQ-accessibility | dimension-backed |
| `has_layout` | DIM-09 | RQ-adaptability | dimension-backed |
| `has_lifecycle` | 无 | RQ-lifecycle | question-only |
| `has_list_render` | 无 | 无 | taxonomy-only，待产品确认 |
| `has_logging` | 无 | RQ-dfx | question-only |
| `has_media` | DIM-06 | RQ-resource | dimension-backed |
| `has_navigation` | 无 | RQ-navigation | question-only |
| `has_network` | DIM-11 | RQ-network、RQ-security | dimension-backed |
| `has_permission_request` | DIM-11 | RQ-security | dimension-backed |
| `has_resource_ref` | DIM-10 | RQ-internationalization | dimension-backed |
| `has_responsive_api` | DIM-09 | RQ-adaptability | dimension-backed |
| `has_state_management` | 无 | RQ-state | question-only |
| `has_storage` | DIM-11 | RQ-security | dimension-backed |
| `has_subscription` | DIM-06 | RQ-resource | dimension-backed |
| `has_taskpool` | DIM-07 | RQ-concurrency | dimension-backed |
| `has_text_display` | DIM-10 | RQ-internationalization | dimension-backed |
| `has_timer` | DIM-06 | RQ-resource | dimension-backed |
| `has_user_input` | DIM-11 | RQ-security | dimension-backed |
| `has_worker` | DIM-07 | RQ-concurrency | dimension-backed |

此外，`RQ-correctness` 始终绑定所有 Primary Unit，不依赖 Tag，也不直接对应某个 Dimension。

## 5. 12 个 Review Question 的路由表

| RQ | 含义 | always_bind | exact Tag 触发器 |
|---|---|---:|---|
| RQ-accessibility | 无障碍行为是否完整且可用 | 否 | `has_interactive_component` |
| RQ-adaptability | 布局是否适配目标设备和窗口变化 | 否 | `has_layout`、`has_responsive_api` |
| RQ-concurrency | 并发与异步行为是否正确且可控 | 否 | `has_async`、`has_taskpool`、`has_worker` |
| RQ-correctness | 改动是否正确且不会引入直接回归 | 是 | 无 |
| RQ-dfx | 诊断、日志和可测试性是否充分 | 否 | `has_logging` |
| RQ-internationalization | 文本和资源使用是否满足国际化要求 | 否 | `has_resource_ref`、`has_text_display` |
| RQ-lifecycle | 生命周期相关行为是否正确配对 | 否 | `has_lifecycle` |
| RQ-navigation | 导航行为和目标状态是否正确 | 否 | `has_navigation` |
| RQ-network | 网络访问和失败处理是否正确 | 否 | `has_network` |
| RQ-resource | 资源是否正确创建、持有和释放 | 否 | `has_file_io`、`has_image`、`has_media`、`has_subscription`、`has_timer` |
| RQ-security | 权限、输入和数据访问是否安全 | 否 | `has_network`、`has_permission_request`、`has_storage`、`has_user_input` |
| RQ-state | 状态管理和状态传播是否正确 | 否 | `has_state_management` |

只有 exact Active Tag 绑定专项 RQ。file-hint Tag 不绑定专项 RQ，Draft Tag 只能影响 Draft RQ
的 shadow 结果。

## 6. 两个最容易看懂的例子

### 6.1 Unit 内精确出现 timer

输入：

```text
exact_tags   = [has_timer]
routing_tags = [has_state_management, has_timer]
```

结果：

```text
dimensions =
  DIM-01, DIM-02, DIM-03, DIM-04, DIM-05, DIM-06, DIM-12

always_check_dimensions =
  DIM-01, DIM-02, DIM-03, DIM-04, DIM-05, DIM-12

retrieval_dimensions = [DIM-06]
routing_dimensions   = [DIM-06]

review_questions = [RQ-correctness, RQ-resource]
```

文件级 state hint 不会给这个 timer Unit 绑定 RQ-state，也不会创建“状态管理 Dimension”。

### 6.2 文件里有 timer，但当前 Unit 没有 timer

输入：

```text
exact_tags   = []
routing_tags = [has_timer]
```

结果：

```text
dimensions =
  DIM-01, DIM-02, DIM-03, DIM-04, DIM-05, DIM-12

retrieval_dimensions = []
routing_dimensions   = [DIM-06]
review_questions     = [RQ-correctness]
```

这正是 file hint 的边界：它可以提醒系统“同文件可能涉及资源管理”，但不能声称当前 Unit 已被
证明涉及 timer。

## 7. 知识 Clause 的 Dimension 是怎么标注的

代码 Unit 与知识 Clause 分别得到 Tag，然后在同一套 Dimension taxonomy 中相遇。

知识侧当前流程：

```text
Clause 标题、正文、父上下文、示例
        │
        ├── identifier / API / decorator / component 提取
        ├── knowledge keyword rules
        └── API alias rules
                │
                ▼
Clause Tags
        │
        ▼
命中非 always-check Dimension.any_tag
        │
        ▼
KnowledgeAnnotation.dimension_ids + provenance
```

当前 `_conditional_dimensions()` 的实际语义是：

```text
不是 always_check
AND Clause Tags 与 Dimension trigger Tags 有交集
→ 写入 Clause Dimension
```

因此自动标注路径：

- 不会给所有知识无条件加 DIM-01～05、DIM-12；
- 通常只自动产生 DIM-06～11；
- 仅有 `has_state_management` 或 `has_lifecycle` 时，`dimension_ids=[]`；
- Publication 保留审核 packet 中的 annotation，不在发布时偷偷重新派生。

### 当前实现的未来风险

`_conditional_dimensions()` 当前没有显式检查 Dimension 是 Active。现有 12 个 Dimension
全部 Active，所以当前没有造成错误；未来引入 Draft/Deprecated Dimension 时必须先补
fail-closed 条件和测试，避免 shadow Dimension 进入正式知识 annotation。

`retrieval_policy` 当前定义的是 Unit 路由政策，不等同于“知识侧是否允许标注该 Dimension”。
未来是否排除 `disabled` Dimension，必须通过独立的 knowledge-indexable 政策明确冻结，不能在
本文擅自推导。

建议 v2 的可发布条件为：

```text
status == Active
AND 有真实 Tag trigger 命中
AND 独立 knowledge-indexable 政策允许
```

即使未来某个 Dimension 同时 `always_check=true`，也只能因为真实 Tag 命中而标到 Clause，
不能因为 always-check 就污染所有知识。

## 8. Dimension 在 Retrieval 中怎样使用

### 8.1 v1 不单独产生候选

Exact Retriever 必须先命中至少一种非 Dimension 信号：

```text
requested rule ID
API
component
decorator
exact Tag
file-hint Tag
keyword
```

有候选后才计算：

```text
dimension_overlap =
  Unit.retrieval_dimension_ids
  ∩ Clause.dimension_ids
```

每重叠一个维度，当前 exact score 增加配置中的 `dimension_overlap` 权重。Dimension 本身不会
让一个原本不存在的 Clause 进入候选池。

### 8.2 向量查询不拼 Dimension 标题

向量查询文本有意不加入“性能、健壮性、安全”等抽象 Dimension 标题，避免这些宽泛词污染
代码语义。候选超过向量阈值后，Dimension overlap 只参与排序。

### 8.3 Evidence 优先覆盖 requested Dimension

Assembler 先尝试为每个 `retrieval_dimension_id` 选择一条带该 Dimension 的候选，再按融合
排序填剩余名额。

```text
covered_dimension_ids =
  requested Dimensions ∩ 已选 Clause Dimensions

uncovered_dimension_ids =
  requested - covered
```

含义边界：

- covered：拿到了该方向的知识，不表示代码合格；
- uncovered：没有选到该方向的知识，不表示代码有问题；
- routing Dimension：保守背景提示，不应被记作正式 coverage。

### 8.4 当前多维补位缺口

如果一条 Clause 同时覆盖 DIM-06 与 DIM-07，当前按维循环可能仍为第二个维度再选另一条
Clause。v2 应维护 `covered_by_selected`，只为尚未覆盖的维度补位，避免浪费预算。

## 9. 为什么不能直接按 Dimension 全量倒排

以下三个 Tag 都属于 DIM-07：

```text
has_async
has_taskpool
has_worker
```

但它们不是同义词。一个普通 `async aboutToAppear()` 不一定需要 TaskPool 或 Worker 规范。
如果只要 requested DIM-07 就把所有 DIM-07 Clause 全召回，会显著增加噪声。

因此 `dimensions-v1` / Retrieval v1 采用“先由具体信号召回，Dimension 再加权和补覆盖”的
策略。若真实 truth 证明需要兜底，建议设计独立的 Retrieval v2：

```text
Clause 有人工审核的目标 Dimension
AND query-clause 语义分数超过校准阈值
AND 每 Dimension 小配额
→ dimension_semantic_backstop
```

它必须使用独立 match scope，不能冒充 exact；无达标结果时保持 uncovered。

## 10. 当前 taxonomy 的明确缺口

七个 Active Tag 没有 Dimension：

```text
has_animation
has_builder
has_lifecycle
has_list_render
has_logging
has_navigation
has_state_management
```

其中 lifecycle、logging、navigation、state 有专项 RQ；animation、builder、list render 连专项
RQ 都没有。

这不应由实现者随意映射。正确治理方式是为每个 Active Tag 强制声明：

```text
dimension_backed
question_only + reason
taxonomy_only + reason
```

如果产品决定新增“状态管理”等 Dimension，必须进行一次完整 `dimensions-v2` 迁移：

1. 人工冻结 taxonomy 决策；
2. 修改配置 schema 与 YAML；
3. 更新 feature-config fingerprint；
4. 更新 Feature Routing Golden；
5. 重建 KnowledgeAnnotation；
6. 对受影响 Clause 重新审核/确认；
7. 重建 PublishedKnowledgeBuild、索引和 E2E；
8. 影子比较后切换，旧版本可回滚。

不得仅编辑 YAML 后继续沿用旧 annotation、旧审核 receipt 或旧索引身份。

## 11. Dimension 与 RQ coverage 应分开量化

当前 E2E 的 `covered=0/9` 只涉及：

```text
DIM-06 × 5
DIM-07 × 2
DIM-08 × 2
```

把 state/lifecycle Clause 加入结果，也不能合法覆盖这些维度。建议 EvidencePack 同时输出：

```text
requested_dimension_ids
covered_dimension_ids
uncovered_dimension_ids

requested_review_question_ids
covered_review_question_ids
uncovered_review_question_ids
```

建议指标：

| 指标 | 含义 |
|---|---|
| Dimension request coverage | 请求的正式检索维度中，有适用知识的比例 |
| RQ knowledge coverage | 本 Unit 要回答的问题中，有适用知识的比例 |
| coverage precision | 被记为覆盖的 Clause 是否真的适用于该维度/RQ |
| uncovered correctness | 没有合格知识时是否正确保留空缺而非强行填充 |
| hint contamination | file hint 是否被错误算作正式覆盖 |

`covered` 只能由人工标注的 Clause 适用性和已选择 Evidence 计算，不能根据“Tag 名看起来相似”
推断。

## 12. 当前测试能证明什么

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_feature_routing_engine.py \
  tests/test_feature_routing_policy.py \
  tests/test_feature_routing_golden.py \
  tests/test_knowledge_annotations.py \
  tests/test_retrieval_golden.py

PYTHONPATH=src .venv/bin/python \
  tools/evaluate_feature_routing_golden.py --require-perfect

PYTHONPATH=src .venv/bin/python \
  tools/evaluate_retrieval_golden.py \
  --strict-baseline tests/golden/retrieval/baseline.json \
  --require-perfect
```

现有 Golden 能证明：

- exact/hint/always-check/retrieval policy 的冻结逻辑正确；
- 配置加载、fingerprint、稳定排序和重放正确；
- 合成知识中的 Dimension 加权、覆盖与输出确定。

不能证明：

- 真实 Parser facts 一定能命中 Tag；
- 当前 Tag→Dimension taxonomy 已覆盖全部业务；
- 真实 Clause 的 Dimension 标注准确；
- Dimension semantic backstop 有效；
- 真实代码与真实知识的 relevance 已合格。

另一个测试边界：Retrieval Golden 可以人工提供 DIM-02～05、DIM-12 Clause 来验证消费逻辑，
但正常 Knowledge 自动标注当前不会生成这些 always-check Dimension。这证明检索器“能消费”，
不证明生产知识管道“会自动生成”。

## 13. 相关文档

- [Tag 识别方法与完整路由表](feature-routing-tags-v1.md)
- [质量缺口与改进方案](../audits/2026-07-14-quality-gaps-and-remediation.md)
- [Feature Routing canonical 文档](../modules/04-feature-routing.md)
- [Knowledge Base canonical 文档](../modules/05-knowledge-base.md)
- [Retrieval canonical 文档](../modules/06-retrieval.md)
