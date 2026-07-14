# ArkTS Code Reviewer 质量缺口与改进方案

> 审计基线：`de1d4b4`  
> 复核日期：2026-07-14  
> 文档性质：基于指定提交与现有 E2E 产物的阶段审计，不是新的运行时合同  
> 正式合同仍以 `docs/architecture/`、`docs/modules/`、`config/` 和代码为准

## 1. 结论

独立审计报告总体质量很高，最重要的判断成立：

- ChangeSet、Parser、ReviewUnit、Feature Routing、Knowledge 治理合同和 Retrieval
  核心链路确实存在，不是纸面架构。
- 身份链、确定性、Golden、fail-closed loader 和 base/head 追踪具有较强工程可信度。
- 当前 E2E 只能证明“链路完整”，不能证明“检索出的知识足以支撑高质量评审”。
- 正式知识、Supporting Context 发现器以及 EvidencePack 到 Finding 的最终闭环，是下一阶段
  的主要价值来源。

但报告中有五处必须纠正，否则容易把改动做在错误的层：

| 原报告判断 | 复核后的准确表述 |
|---|---|
| 约 90% 的证据关系是噪声 | 60/67 关系**包含** `file_hint`，其中纯 hint-only 为 38/67；实际噪声率必须由人工相关性标注确认 |
| 给 `tags.yaml` 增加 `connection.`、`avSession.` 前缀即可修复 | 真实信号主要在 `calls` 和 `import_uses`，当前 Matcher 不读取这两个字段；只改 API 前缀不会生效 |
| Dimension 应直接成为第三条倒排召回路径 | “Dimension 不单独召回”是 Retrieval v1 的明确合同；若增加兜底，必须是带语义阈值、小配额且可 abstain 的 v2 策略 |
| state/lifecycle 没有 Dimension，因而永远无法检索 | 它们仍可通过 exact Tag 和 RQ 检索；真正缺少的是“RQ 覆盖度”的知识标注与度量，不能让 state 条款冒充 DIM-06/07/08 |
| `aboutToDisappear` 应作为 timer Unit 的 Supporting | 它在当前 Diff 中本身是 Primary；正确做法是 Primary–Primary 强关系分组，只有未修改 helper 才能成为 Supporting |

此外，复核发现两个原报告未明确指出的问题：

1. 真实 symbol 是 `Index.aboutToDisappear`，配置写的是 `aboutToDisappear`；当前完整字符串
   匹配会漏掉生命周期 Tag，而手工构造的 Feature Routing Golden 没有覆盖这种真实形状。
2. Evidence assembler 在一个 Clause 同时覆盖多个 Dimension 时，可能仍为后续 Dimension
   重复补选另一个 Clause，浪费紧张预算。

## 2. “万无一失”在本项目中的工程含义

任何检索、静态分析或 LLM 系统都不能承诺字面意义上的零错误。本方案把“万无一失”落实为
以下可执行约束：

1. **不猜测**：无法证明 owner、模块身份或适用性时 abstain，并输出结构化诊断。
2. **不污染强证据**：弱信号可以扩候选，但不能冒充 Unit 精确事实或 Finding 引用依据。
3. **先有真值再改算法**：每项质量优化都必须先建立真实正反例和 expected，不能由当前实现
   反向生成真值。
4. **版本化变更**：Tag、Dimension、Clause 正文、索引和 Prompt 合同变化均升级版本与
   fingerprint，不原地偷偷改语义。
5. **影子发布**：新信号、新 Tag、新召回路径先进入 Draft/shadow，达到门禁后再转 Active。
6. **可回滚**：新索引使用不可变 build + alias 切换；新配置保留旧版本，出现回归可立即切回。
7. **分层量化**：分别测 Parser、Unit、Tag、Context、Retrieval、Evidence 和 Finding，不能用
   “全量测试通过”替代业务准确率。

## 3. 已验证的当前快照

| 项目 | 当前事实 | 说明 |
|---|---:|---|
| 全量 pytest | 712 passed / 3 skipped | skip 为需要外部 DB/GPU 的集成路径；这是审计时点结果 |
| Retrieval Golden | 36/36 perfect | 证明冻结合同和合成 truth 正确，不代表真实知识质量 |
| FileAnalysis Golden | 15/15 perfect | 证明 occurrence/owner/span 冻结合同 |
| E2E ReviewUnit | 13 ChangeAtom → 15 Unit，0 未分配 | 当前一个真实样例与人工 expected 匹配 |
| E2E 索引 | 109 Clause，全部 Draft | 仅用于 staging/evaluation，不可当生产 Evidence |
| 索引来源覆盖 | 24 个 Seed 中仅 11 个出现 | 其余 13 个的缺失原因不能统一假定为“没审核” |
| Evidence 关系 | 67 | 19 个 distinct Clause |
| `file_hint` | 60/67 含 hint；38/67 为纯 hint-only | “含 hint”不等于“已证明无关” |
| `unit_exact` | 1/67 | 且来自摘要/代码文本 keyword 子串，不是结构化精确事实 |
| vector | 29/67 | 22/29 相似度低于 0.35 |
| Dimension coverage | 0/9 | requested 为 DIM-06×5、DIM-07×2、DIM-08×2 |
| budget diagnostic | 15/15 Unit | 当前诊断只说明候选未装完，不能说明遗漏的是相关知识 |
| 最终评审 | 无运行闭环 | Rules、Review Prompt、LLM、Finding validator 尚未实现 |

## 4. 优先级总表

| ID | 问题 | 优先级 | 是否阻塞最小可用评审 | 推荐工作包 |
|---|---|---:|---:|---|
| KB-01 | 没有生产可用的 Baselined 知识索引 | P0 | 是 | K1 |
| KB-02 | 试点代码域知识覆盖不足 | P0 | 是 | K1 |
| FR-01 | Matcher 未消费 `calls/import_uses`，平台能力静默漏 Tag | P0 | 是 | F1 |
| FR-02 | qualified symbol 与裸 symbol 配置不一致 | P0 | 是 | F1 |
| CTX-01 | Supporting Context 只有合同，没有自动发现生产者 | P0 | 是 | C1 |
| RET-01 | hint-only 候选可以直接进入正式 Evidence | P0 | 是 | R1 |
| TAX-01 | Dimension coverage 与 RQ coverage 混为一谈 | P1 | 是 | K2/R1 |
| RET-02 | 当前没有安全的 Dimension 语义兜底 | P1 | 否；先修 exact/语料 | R2（可选 v2） |
| RET-03 | 预算平均分配且诊断信息不足 | P1 | 是 | R1 |
| RET-04 | 文本 keyword 被错误标为 `unit_exact` | P1 | 是 | R1 |
| RET-05 | 多 Dimension Clause 可能造成重复补选 | P1 | 否 | R1 |
| KB-03 | Clause 正文保留 Markdown 链接与转义 | P1 | 是 | K1 |
| KB-04 | Clause 粒度与可执行性缺少真实质量门禁 | P1 | 是 | K1 |
| REP-01 | 报告 Markdown 转义触发 KaTeX ParseError | P1 | 是（展示） | K1 |
| VEC-01 | 真实向量召回质量与阈值尚未校准 | P1 | 是 | R1 |
| QRY-01 | 大 Unit 的检索摘要可能遗漏关键语义 | P1 | 是 | R1 |
| LLM-01 | EvidencePack 到结构化 Finding 的闭环不存在 | P0 | 是 | L1 |
| EVAL-01 | 缺少真实 Tag、Context、Retrieval、Finding 真值集 | P0 | 是 | 各工作包先行条件 |
| PAR-01 | Parser 真实语料仍有 ERROR/missing，L1 不等于无错误 | P2 | 否；已有降级合同 | 监测/Parser v2 候选 |
| RU-01 | ReviewUnit 真实准确率主要由单个 E2E 支撑 | P1 | 是（量化） | EVAL-01 |
| QA-01 | 报告中的静态检查没有可机读执行证明 | P2 | 否 | Q1 |
| IN-01 | 生产调用方仍需提供结构化 ChangeSet | P2 | 否（已知边界） | 后续 Input/Git 集成 |

## 5. 逐项改进方案

### KB-01：没有生产可用的 Baselined 知识索引

**当前事实**

- E2E 索引中的 109 条 Clause 全部是 `Draft`。
- 现有 publication 代码能够拒绝未完成治理的知识，因此这是“生产资产尚未发布”，不是
  fail-closed 治理失效。

**风险**

Draft 可以用于评估管道，但不能作为高严重度 Finding 的正式规范依据。若直接放开 LLM，
要么生成无依据的泛化意见，要么被证据 validator 拒绝。

**稳健方案**

1. 生成逐来源状态矩阵：
   `Source → Clause → round1 → round2 → consensus → curation → publication`。
2. 对每条未发布 Clause 记录唯一、互斥的阻塞原因：
   `no_clause / missing_round / rejected / conflict / correction_required / duplicate / accepted`。
3. 仅允许满足以下全部条件的 Clause 转为 Baselined：
   - 来源在 Source Registry allowlist；
   - revision 和 content hash 固定；
   - 两轮审核 receipt 完整；
   - consensus 明确；
   - curation 决策接受；
   - 正文、标签、适用范围和 provenance 校验通过。
4. 生成不可变 `PublishedKnowledgeBuild`，先建立 staging 索引，再做完整回读、身份、计数、
   维度和查询 smoke test。
5. 通过 alias 原子切换到 production；旧 build 保留，支持快速回滚。

**禁止的捷径**

- 不得把 Draft 批量改名为 Baselined。
- 不得用“Clause 数量达到目标”替代内容审核。
- 不得从 E2E 当前命中结果反向批准知识。

**验收门禁**

- PostgreSQL `current` alias 只允许指向 `origin=publication` 的不可变 KnowledgeIndex。
- 由该索引生成的 EvidencePack 必须显示 `production_eligible=true`。
- production 索引 Draft 数量为 0。
- 每条记录能追溯 source/revision/hash/origin span/review receipts/curation decision。
- 同一 build 重建身份完全一致；故障演练可切回前一 alias。

### KB-02：试点代码域知识覆盖不足

**当前事实**

- 24 个 Seed 中只有 11 个出现在 109 条评估 Clause 中。
- timer、systemTimer、emitter、Global timer 等已登记来源未进入当前索引。
- 现有 24 个 Seed 本身没有完整的 `@ohos.net.connection` 和 AVSession 专项资料。

**稳健方案**

1. 先定义试点场景，不先定义 Clause 数：
   - timer 创建、重置、销毁与生命周期配对；
   - emitter/sensor 订阅与取消订阅；
   - 网络连接、失败、重试、退避、监听注销；
   - AVSession/播放器资源创建、事件监听、释放和生命周期；
   - ArkUI state/lifecycle 规范。
2. 对现有 13 个未入索引 Seed 逐文档定位真实原因；优先完成 timer、systemTimer、emitter、
   Global timer。
3. 对现有 Seed 不覆盖的 connection/AVSession，通过 Source Registry 正式增加权威文档或
   API 定义。不得用样例代码或模型常识冒充规范来源。
4. 为每个场景建立“应召回/不应召回” Unit–Clause truth，不以关键词相同作为相关性真值。
5. 达到真实检索门禁后才扩大领域；未覆盖领域必须显式返回 `knowledge_gap`，不能强行填充。

**验收门禁**

- 每个试点场景至少有一份权威来源和已发布 Clause；数量只作为运营指标，不作为质量保证。
- timer、network、AVSession 的正例 Recall@5、Precision@5、nDCG@5 达到预先冻结阈值。
- 近似反例不被误召回，例如普通 `.on()`、本地 `connection` 对象、普通 async 生命周期函数。
- E2E 的 Dimension/RQ coverage 只能由真实适用的 Clause 提升。

### FR-01：平台调用信号没有进入 Tag Matcher

**当前事实**

`UnitFactScope.unit_exact` 已包含 `calls`、`import_uses`、`field_reads`、`field_writes` 等字段，
但当前 Matcher 只消费 components、apis、decorators、attributes、symbols、syntax 和
resource references。真实 E2E 中：

- `connection.createNetConnection`、`connection.getAllNets` 在 `calls`；
- `@ohos.net.connection#default` 在 `import_uses`；
- `this.session?.setAVMetadata` 等 AVSession 行为也在 `calls`；
- 它们不在 `apis`，因此新增 `any_api_prefix` 不会命中。

**稳健方案：FeatureSignalNormalizer v2**

1. 不修改冻结 Parser v1 默认行为；在 `UnitFactScope` 与 Feature Matcher 之间增加版本化
   信号归一层。
2. 增加有明确语义的触发器：
   - `any_import_source` / `any_import_use`；
   - `any_call` / `any_call_prefix`；
   - `any_symbol_leaf`；
   - `all_of` 与 `any_of` 组合。
3. 平台能力优先由“导入身份 + 调用接收者”共同证明。例如网络正例需要：

   ```text
   import source = @ohos.net.connection
   AND call receiver resolves to that local import binding
   ```

4. 建立版本化 API Symbol Catalog，至少保存模块、导入形式、alias、canonical API、Tag、
   平台/API level。
5. 无法证明 `this.session` 的类型时，不因方法名相似就推断 AVSession；保留未分类诊断，等待
   类型/owner 证据。
6. 新触发器先以 Draft/shadow 运行，真实 P/R 达标后升级 `tags-v2` 并转 Active。

**验收门禁**

- `@ohos.net.connection` alias import + 调用准确产生 `has_network`。
- 同名本地 `connection`、参数遮蔽和别名反例不产生 `has_network`。
- AVSession 有模块身份佐证时产生 `has_media`；无类型佐证的 `this.session` 不做强推断。
- 每个 match 保存原始 signal、归一 signal、scope 和 rule ID，可完整解释。

### FR-02：qualified symbol 导致生命周期等 Tag 漏标

**当前事实**

真实 Unit symbol 是 `Index.aboutToDisappear`，当前配置只登记 `aboutToDisappear`，Matcher 使用
大小写敏感的完整字符串相等。现有 Golden 手工注入裸 symbol，因此没有暴露集成缺口。

**稳健方案**

1. 增加边界感知的 `symbol_leaf`：只在 `.` 分段边界取最后一段，不能做任意 substring。
2. 同时保留 `Index.aboutToDisappear` 原始值与 `aboutToDisappear` 归一值。
3. 生命周期、`ForEach/LazyForEach/Repeat`、`ThreadWorker` 全部增加 qualified/unqualified
   正反例。
4. 新增 Parser → FileAnalysis → ReviewUnit → UnitFactScope → FeatureRouter 集成 Golden，
   不再只测试人工构造的 `ScopedFacts`。

**验收门禁**

- `Index.aboutToDisappear` 精确得到 `has_lifecycle → RQ-lifecycle`。
- `Index.notaboutToDisappear`、字符串文本和注释不命中。
- qualified/unqualified 输入得到相同 Tag ID，但 provenance 保持不同原始信号。

### CTX-01：Supporting Context 自动发现器缺失

**当前事实**

ContextPlan 已定义 `direct_call/direct_caller/state_access/lifecycle_pair` 等关系，也能消费注入的
candidate/edge；生产链没有生成这些关系的发现器。当前 E2E 只有 base/head
`change_correspondence`，`supporting_segments=0` 是当前实现的结构性结果。

**稳健方案：同文件、深度 1、精确边界的 v1 发现器**

1. 按 `source_ref_id + host owner` 建 declaration、field region 和 fact occurrence 索引。
2. `this.foo()` 只解析到同 host 唯一的 `Host.foo`；0 个或多个候选时 abstain 并记录原因。
3. call occurrence 生成 `direct_call/direct_caller`。
4. host-qualified field read/write 与 field region 生成 `state_access`。
5. lifecycle pair 使用显式配置，并要求同 host，不凭名称相似自动配对。
6. 目标已是 Primary：保留 Primary 身份，生成 Primary–Primary strong edge 并进入同一
   ChangeGroup。
7. 目标未修改：才生成 SupportingCandidate；span 必须严格等于 Parser 的 declaration/region
   边界。
8. 每条 edge 保存 occurrence ID、解析规则和 source revision；不使用 `same_file/same_host`
   作为实际相关性证据。
9. v1 不递归、不跨文件，最大深度固定为 1；跨文件 import graph 单独进入后续版本。

**验收门禁**

- 当前 E2E 出现准确的 call/lifecycle/state relation edge，但已经改动的方法仍是 Primary。
- 单独建立“改动方法调用一个未修改 helper”的 fixture，Supporting Recall=1.0。
- 同名方法、多 host、动态调用、无法解析 receiver 的负例全部 abstain。
- `supporting_segments=0` 本身不是失败；只有人工 truth 中存在必需 Supporting 且未找到才失败。

### RET-01：hint-only 候选进入正式 Evidence

**当前事实**

`routing_tag` 当前可以单独使 Clause 进入 exact candidate pool。E2E 中 60/67 关系含
`file_hint`，38/67 是纯 hint-only。由于同文件的状态装饰器会传播给每个 Unit，某些状态条款
在 15/15 Unit 重复出现。

**稳健方案**

1. `file_hint` 只扩展候选池，不允许单独生成可供 Finding 引用的 `EvidenceClause`。
2. 候选至少满足以下一项佐证才进入正式 Evidence：
   - 结构化 `unit_exact` Tag/API/component/decorator；
   - 达到按模型与领域校准的语义阈值；
   - requested rule ID；
   - 明确且已审核的 RQ/Dimension 适用性与额外语义佐证。
3. 业务需要展示 hint-only 时，将其放入独立 `context_candidates`，明确
   `not_finding_evidence=true`。
4. 不把跨 Unit 重复降权当主修复；同一规范可能合理适用于多个 Unit，核心是逐 Unit
   适用性。
5. 通过标注集校准阈值与配额，不用 E2E 当前分数拍脑袋决定。

**验收门禁**

- 正式 Evidence 中 hint-only 数量为 0，或仅存在经过显式产品批准的独立弱证据类型。
- 15-Unit 标注集报告 Precision@5、Recall@5、nDCG@5 和 hint-only accepted rate。
- timer Unit 的无关 state Clause 误召回率达到冻结阈值。
- Finding validator 拒绝仅引用 `file_hint/context_candidate` 的高严重度结论。

### TAX-01：Dimension coverage 与 Review Question coverage 混淆

**当前事实**

- `has_state_management` 和 `has_lifecycle` 没有映射到现有 12 个 Dimension，但分别绑定
  `RQ-state` 和 `RQ-lifecycle`。
- RQ 与 Dimension 是两套不同抽象，不要求一一对应。
- 本次 9 个 requested Dimension 实例是 DIM-06、DIM-07、DIM-08；state/lifecycle 条款
  不应被计作这些维度的覆盖。

**稳健方案**

1. 保持当前 12 Dimension 语义不变，不为了提升数字把 state/lifecycle 塞进错误维度。
2. 给 KnowledgeAnnotation 和 EvidencePack 增加 `review_question_ids`，分别输出：
   - requested/covered/uncovered Dimension；
   - requested/covered/uncovered Review Question。
3. 为每个 Active Tag 增加显式分类门禁：
   - `dimension_backed`；
   - `question_only`；
   - `taxonomy_only`。
4. 当前七个无 Dimension Tag 必须逐个记录设计理由；三个既无 Dimension 又无专项 RQ 的
   Tag（animation、builder、list render）必须明确是 taxonomy-only 还是配置缺口。
5. 若产品确认“状态管理”需要新评审维度，新增 `dimensions-v2`，同步 schema、fingerprint、
   Golden、知识重标和索引迁移；不原地改变 DIM-05/DIM-06。
6. Knowledge 自动派生 Dimension 时显式要求 `status=Active`；当前全是 Active，尚未出错，
   但未来 Draft Dimension 不能进入正式 annotation。`retrieval_policy` 是 Unit 路由政策，
   是否允许某维度进入知识索引应由独立 knowledge-indexable 政策冻结。

**验收门禁**

- state Clause 可覆盖 `RQ-state`，但不能冒充 DIM-06/07/08。
- 配置 loader 拒绝没有分类说明的 Active Tag。
- E2E 同时报告 Dimension coverage 与 RQ coverage，分母和来源可追溯。

### RET-02：Dimension 语义兜底的正确边界

**当前事实**

Retrieval v1 明确规定 Dimension 只参与加权和覆盖，不单独产生候选。这不是实现漂移。
`has_async`、`has_taskpool`、`has_worker` 虽同属 DIM-07，但并不是同义词；普通 async 代码不应
无条件召回 TaskPool 专项规范。

**稳健方案**

先补知识和 exact Tag。只有真实 truth 证明仍存在同维度语义漏召回时，才设计 Retrieval v2：

1. Clause 必须带人工审核过的目标 Dimension。
2. Query 与 Clause 仍必须超过独立校准的语义阈值。
3. 每个 requested Dimension 只有很小的候选 quota。
4. 匹配类型标记为 `dimension_semantic_backstop`，不能标为 exact。
5. 没有达标候选时保留 `uncovered`，不得强行填满。
6. v1 与 v2 影子对比 Precision/Recall 后再切换。

**验收门禁**

- 建立 `has_async` 查询、TaskPool-only Clause 的正负成对样例。
- v2 在提升 dimension Recall 的同时不得让 Precision@5 低于冻结下限。
- backstop 命中可单独统计、关闭和回滚。

### RET-03：预算平均分配且诊断不可解释

**当前事实**

8192 token 按 15 Unit 机械均分为约 546/547；15/15 Unit 都报告 `budget_exhausted`。当前诊断
只说明至少一个融合候选未装入，却使用了“relevant Clause”措辞，系统实际上并不知道它是否
相关。

**稳健方案**

1. 将诊断拆成：
   - `candidate_truncated_by_budget`；
   - `result_limit_reached`；
   - `required_dimension_uncovered_by_budget`。
2. 每条诊断记录 eligible/selected 数、used/limit token、第一个未装候选的 rule ID、score、
   match scope、similarity，以及是否丢失强 exact evidence。
3. 使用确定性的两阶段预算：
   - 所有可 dispatch Unit 先获最小保障；
   - 余额依据高置信候选需求、exact signals、RQ 数、changed lines 和 Primary 重要度分配。
4. 没有高置信候选的 Unit abstain，不以低质量候选填满预算。
5. 在 2K/4K/8K/16K 做 budget-quality curve，选择质量拐点而不是让诊断数量好看。

**验收门禁**

- 相同输入与预算输出完全确定。
- 强 exact Evidence 不会被弱 hint 候选挤出。
- 报告每个预算档的 Precision@5、Recall@5、覆盖率和 token 使用率。

### RET-04：文本 keyword 冒充 `unit_exact`

**当前事实**

当前 keyword 在 `intent_summary + semantic_code_excerpt` 上做大小写归一后的子串匹配，却记录
为 `unit_exact`。E2E 唯一一条 unit-exact 关系就是这种文本命中。

**稳健方案**

1. 只有结构化 occurrence/fact 的边界感知匹配可以使用 `unit_exact`。
2. intent/code excerpt 文本命中使用独立 scope，例如 `context_text` 或现有 `semantic`。
3. 从 intent 中排除 Review Question 标题，避免 Clause keyword 因系统自己拼入的问题文本
   产生循环命中。
4. 标识符使用 token/identifier boundary，不做任意 substring。
5. 增加 `timer/time`、`state/statement`、短关键词、大小写、中文词界和代码字符串反例。

**验收门禁**

- 结构化强匹配与文本弱匹配在 Evidence 中可独立过滤和计分。
- 不存在“只有摘要子串命中却标为 unit_exact”的结果。

### RET-05：多 Dimension Clause 造成不必要补选

**当前事实**

Assembler 按 requested Dimension 逐一补 Clause。如果先选中的一个 Clause 同时覆盖 DIM-06 和
DIM-07，后续循环仍可能为 DIM-07 再选一条，浪费预算。

**稳健方案**

每次选入 Clause 后，更新 `covered_by_selected`；后续只对尚未覆盖的 requested Dimension
补选。再进入 RRF 主排序填充剩余预算。覆盖必须按 Clause 的已审核 Dimension 计算，不能按
file hint 推断。

**验收门禁**

- 新增 tight-budget Golden：一个多维 Clause 应一次覆盖两个维度，不额外补选。
- 顺序变化不改变选中集合、coverage 和 token 使用。

### KB-03 与 REP-01：Clause Markdown 污染和 KaTeX ParseError

**当前事实**

Clause parser 当前基本保留 Markdown inline 原文；E2E Evidence 中存在链接与 `\@State` 等
转义。报告表格又把 `[`/`]` 变为 `\[`/`\]`，某些 Markdown 渲染器会把它识别为 display math，
最终由 KaTeX 尝试解析 `\@State` 并报错。

**稳健方案：入库与展示双保险**

1. 入库不使用正则剥 Markdown，使用 markdown-it inline child tokens 生成 canonical plain text。
2. link 只在 `display_text/embedding_text` 中保留锚文本；URL 存入 `source_links` 元数据。
3. code span 保留代码文本，Markdown escape 正确解码，source span/provenance 不变。
4. 明确区分 `display_text`、`embedding_text`、`source_links`；若变更 schema，则升级版本。
5. REPORT 表格使用 HTML entity（如 `&#91;`、`&#93;`、`&#124;`），不生成 `\[`/`\]`。
6. 文本变化会改变 Clause/hash/embedding；必须重建、重审必要内容并发布新索引，不能伪装成
   旧 identity。

**验收门禁**

- 新索引 display/embedding text 中没有 Markdown link target。
- 原始 URL 仍可由 `source_links` 追溯。
- 用 `[@State](...)`、`[\@Once](...)`、code span、pipe、方括号和中文建立
  parse→index→Evidence→REPORT 全链回归。
- GitHub/VS Code 预览无 KaTeX ParseError。

### KB-04：Clause 粒度与可执行性缺少真实质量门禁

**当前事实**

现有 Clause 构建有结构与身份门禁，但真实评估集中仍存在很短的条款。短不必然等于差，固定的
最小 token 数也可能误删简洁、完整的强制规范；真正缺少的是“该 Clause 能否独立解释并用于
判断代码”的人工真值。

**稳健方案**

1. 给 Clause 审核增加四项独立评分：语义完整性、单一规范原子性、上下文自足性、可执行性。
2. 过短片段优先尝试与同 heading 下的前后段合并；过长段落按规范义务而不是固定字符数拆分。
3. 标题、限定条件、例外、版本和 API level 必须随正文进入 Clause 或结构化 applicability。
4. `embedding_text` 可做检索归一，但 `display_text` 不得丢失判断所需限定条件。
5. 由双审/curation 决定 `accept / merge / split / reject`，模型建议不能直接发布。

**验收门禁**

- 随机抽样与高频命中 Clause 的人工可执行性通过率达到冻结阈值。
- 不存在只有标题、链接列表、半句话或缺少前提的 production Clause。
- merge/split 后 provenance 可追溯，旧 identity 不复用。

### VEC-01：真实向量召回尚未校准

**当前事实**

E2E 29 条 vector 关系中 22 条相似度低于 0.35；现有 12-case embedding 指标是模型选型证据，
不是当前真实 ArkTS Unit–Clause 相关性证明。代码摘要与中文规范的跨语言/跨模态差异也可能
削弱相似度。

**稳健方案**

1. 使用 EVAL-01 的真实 Unit–Clause 标注集，按 timer/network/AVSession/state 等领域分别画
   similarity 正负分布，不能从单个 E2E 拍阈值。
2. 分别评估原代码、结构化 facts、中文 intent、混合 query；选用对真实 nDCG/Recall 最优且
   稳定的表示。
3. 阈值按 embedding model/version 与领域冻结；模型或 query 模板变化即重新校准。
4. 向量只是一条候选路径。低于阈值时 abstain，不因候选数量不足自动降阈值。
5. 记录 model digest、维度、归一化方法、query template version 和向量缓存身份。

**验收门禁**

- 报告各领域 Precision/Recall/nDCG 与置信区间，不只报告总体均值。
- 随机负例、同词不同义和跨领域近似条款进入 hard-negative 集。
- threshold 前后结果可重放；换模必须建立新索引并影子对比。

### QRY-01：大 Unit 的检索摘要可能遗漏关键语义

**当前事实**

RetrievalRequest 对代码 excerpt 有行数/字符预算。方法级 Unit 通常够用，但较大的 UI block
可能经过抽样，改动附近的接收者、清理逻辑或条件分支可能没有进入向量 query。

**稳健方案**

1. query 由多段确定性视图组成：changed lines、前后语句、owner signature、关键 calls/fields、
   必需 Supporting 摘要；不得只均匀抽样全文。
2. changed lines 永远保留；围绕 change atom 分配局部窗口，再按结构化信号补 owner 上下文。
3. 每段标记来源 span，超预算时记录丢弃原因。
4. 比较 single-query 与 multi-query/fusion，在真实 truth 上选择，不提前假定后者一定更好。

**验收门禁**

- Golden 精确比较 query 中保留的 changed lines、span 和截断诊断。
- 大 UI block truth 上 Recall@5 不低于方法 Unit 的冻结下限。
- query 不包含无归属的整文件事实文本。

### LLM-01：最终评审闭环不存在

**当前事实**

Rules、Review Prompt、LLM 调用和 Finding 仍只有设计，没有可运行主链。中间产物再完善也不能
直接成为用户可读的代码评审结论。

**稳健方案：小而真的 fail-closed 垂直切片**

1. 先冻结 `finding-v1`：Finding 必须包含 location、severity、category、claim、impact、
   recommendation、evidence references、confidence 和 diagnostics。
2. Prompt 只输入：当前 Primary、必要 Supporting、明确分域的 exact facts/file hints、
   已发布 Evidence；不得把 Draft 或 hint-only 当规范依据。
3. LLM 输出严格 JSON schema；解析失败、未知字段、悬空 ID、越界行号一律拒绝。
4. Evidence validator 至少执行：
   - 引用 Clause 存在且 production eligible；
   - Finding location 位于允许 source span；
   - high/critical 必须有强 Evidence 或确定性 Rule；
   - file hint 不能独立支撑事实断言；
   - 同一 Finding 的 claim 与引用适用范围相容。
5. 不合格 Finding 降级为 diagnostic/abstain，不自动改写为看似确定的意见。
6. 先在 timer/subscription/lifecycle 小域运行，用人工核对的 10～30 个 Finding truth 做门禁，
   达标后扩域。

**验收门禁**

- E2E 能产出结构化 Finding 或明确 abstain，而不是自由文本。
- 无引用 high/critical、Draft 引用、悬空引用、越界 location 均 fail-closed。
- 人工 truth 上分别报告 Finding Precision、Recall、severity accuracy、citation correctness。

### EVAL-01：真实质量真值不足

**当前事实**

现有 Golden 对合同、确定性和人工构造事实覆盖很好，但真实跨层质量证据仍小：ReviewUnit 当前
主要是一份真实 E2E，Feature Routing Golden 使用手工事实，Retrieval Golden 使用合成知识，
最终 Finding 没有 truth。

**稳健方案**

建立四组彼此独立、不可由实现生成 expected 的 truth：

1. **Tag truth**：每 Tag 10～20 个真实正例与 10～20 个近似反例，包含 alias、qualified name、
   exact/hint 和 degraded parser。
2. **Context truth**：每个 changed Primary 应关联哪些 Primary/Supporting、关系类型、为什么；
   含同名 owner 和歧义 abstain。
3. **Retrieval truth**：逐 Unit 标注 Clause 为 relevant/partially relevant/irrelevant，并注明
   适用 RQ/Dimension。
4. **Finding truth**：问题位置、结论、严重度、所需 Evidence、允许 abstain 的边界。

所有集合保存 reviewer、时间、source revision 和 disagreement；双人不一致时不自动取多数，
进入 adjudication。

**统一量化**

| 层 | 核心指标 |
|---|---|
| Tag | per-tag P/R/F1、macro/micro P/R、unknown signal rate |
| Context | Primary coverage、Supporting Recall/Precision、edge type accuracy、abstain correctness |
| Retrieval | Recall@5、Precision@5、MRR、nDCG@5、RQ/Dimension coverage、noise rate |
| Finding | Precision/Recall、severity accuracy、citation correctness、unsupported claim rate |
| 系统 | end-to-end useful finding rate、false high-severity rate、abstain rate、确定性 |

### PAR-01：L1 成功不等于 AST 无 ERROR/missing

**当前事实**

Parser Golden 合同稳定，但已有 R63 基线明确记录部分文件包含 ERROR/missing warning。
`parser_layer=L1` 只表示 sidecar 成功，不表示语法树完全无错误。

**稳健方案**

- 不因下游 Tag/检索问题重开冻结 Parser v1。
- 所有 Unit、Context、Retrieval 和 Finding 保留 parser quality/warnings；错误影响 owner 或 span 时
  降级或阻止高置信结论。
- 将新真实失败最小化为独立 Parser v2 candidate，不修改 v1 Golden expected。
- 持续报告 L1 success、ERROR、missing、declaration/occurrence coverage 四组不同指标。

### RU-01：ReviewUnit 真实准确率样本不足

**当前事实**

当前 E2E 的 15 Unit 与人工 expected 完全匹配，证明该样例很好；它不能推出所有真实 Diff 的
Primary/Supporting 准确率都是 1.0。“合同边界内完成”与“真实语料风险为零”不是一回事。

**稳健方案**

- 再选 3～5 个定点真实 Diff，覆盖多文件、多 owner、同名 UI、deletion、large build、
  degraded parse 和 base/head 关系。
- 人工标注全部 Primary、owner、span、change atoms、必要 Supporting 与应 abstain 项。
- 分别测 Primary Precision/Recall、changed-line assignment、context exactness、relation accuracy；
  不只统计 Unit 数相等。
- 新样本只定点读取已登记来源，不递归扫描外部仓库。

### QA-01：静态检查声明缺少机器证明

**当前事实**

当前全仓 Ruff 仍有两个存量问题；REPORT 只写了 “ruff / git diff --check passed”，没有记录
命令、scope、commit、时间和 exit code。该文字可能只代表当时修改文件，不能据此推断历史报告
造假，但确实不可审计。

**稳健方案**

1. 每条质量检查单独记录完整命令、工作目录、commit、时间、scope、exit code。
2. 明确区分 `full repository` 与 `changed files only`。
3. pytest、Ruff、mypy、diff-check 生成机器可读 attestation JSON；REPORT 只渲染该产物，
   不硬编码“passed”。
4. 失败结果照实展示，不因 E2E 主链成功而合并成 PASS。

## 6. 推荐实施顺序

```text
并行准备
├── EVAL-01：冻结 Tag / Context / Retrieval truth
├── K1：KB-01 + KB-02 + KB-03 + REP-01
├── F1：FR-01 + FR-02
└── C1：CTX-01
        │
        ▼
R1：RET-01 + TAX-01 + RET-03 + RET-04 + RET-05
        │
        ├── 达不到召回目标时，再评估 RET-02 的 v2 backstop
        ▼
L1：Finding schema + Prompt + validator + 小域 LLM 闭环
        │
        ▼
Q1：全链质量 attestation、生产 alias、回滚演练
```

不得反过来先大规模调用 LLM：在正式知识、Evidence 去噪和引用 validator 未就绪前，LLM 输出
只能作为实验结果，不能作为第一阶段合格评审。

## 7. 第一阶段最小交付门禁

第一阶段不应以“代码写完”定义完成，而应同时满足：

- production 知识索引只含 Baselined、可追溯 Clause；
- timer/network/AVSession 试点域有权威知识与真实检索 truth；
- Parser→UnitFactScope→FeatureRouter 集成集通过，已知 connection/lifecycle 漏标修复；
- 必需的未修改同文件 helper 能被发现，歧义时安全 abstain；
- 正式 Evidence 不含未经佐证的 hint-only 关系；
- 真实标注集上的 Precision@5、Recall@5、nDCG@5 达到冻结阈值；
- Dimension coverage 与 RQ coverage 分开计算；
- EvidencePack→LLM→Finding→validator 能产出合格结构化结果或明确 abstain；
- unsupported high/critical Finding 数量为 0；
- 所有门禁带 commit、命令、scope、exit code 和机器可读产物；
- 重跑身份和输出确定，旧配置与旧索引可回滚。

## 8. 明确暂不在本轮解决的边界

- Parser v1 默认行为与 Parser Golden 不因 Tag 覆盖问题重开。
- 跨文件 Supporting/import graph 在同文件 v1 通过后再做。
- Dimension-only 无条件倒排不进入 Retrieval v1。
- 不用样例代码、模型回答或 `file_hint` 充当规范 Evidence。
- 原始 Git diff 文本解析、GitCode 写回、Webhook/任务编排仍属于 Input/Output 后续模块。
- embedding 换模不能替代语料、标注和召回策略修复。

## 9. 关联文档

- [Tag 识别方法与完整路由表](../reference/feature-routing-tags-v1.md)
- [Dimension 推导方法与完整路由表](../reference/feature-routing-dimensions-v1.md)
- [Feature Routing canonical 文档](../modules/04-feature-routing.md)
- [Knowledge Base canonical 文档](../modules/05-knowledge-base.md)
- [Retrieval canonical 文档](../modules/06-retrieval.md)
- [E2E 报告](../../E2E_test_example_1/REPORT.md)
