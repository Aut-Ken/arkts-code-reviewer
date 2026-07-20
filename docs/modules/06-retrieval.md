---
title: 06 Retrieval 检索模块
status: canonical
implementation: partial
updated: 2026-07-20
---

# 06 Retrieval 检索模块

## 1. 模块职责

接收 Unit 级代码特征，从版本化知识索引返回最相关、可引用、可调试的 Evidence Pack。

```text
Unit Query
-> 路由
-> 精确/语义召回
-> 融合和重排
-> token 预算组装
-> Evidence Pack
```

不解析代码、不判断代码好坏、不生成最终修改建议。

## 2. 当前状态

Retrieval v1 core/runtime 已实现，并通过 fixture-backed 本地端到端验证；生产知识激活尚未完成：

- 从正式 `AnalysisResult + ContextPlanResult` 构造 `retrieval-request-v1`；
  `FeatureRoutingResult` 必须已经绑定在 `AnalysisResult` 中并可重放，实现不会读取兼容
  `AnalysisResult.retrieval_query`。
- 生产在线路径只接受 Knowledge 模块发布的 `Baselined` Clause；Draft、原始 Markdown 和外部 clone
  不能进入生产索引。显式 opt-in 的 evaluation fixture 可携带 Draft，但固定非生产资格。
- 支持 rule ID、canonical API/alias、组件、装饰器、Tag 和代码关键词的确定性召回，
  以及本地 FastEmbed 向量召回、RRF 融合、适用性过滤和真实知识 token 裁剪。
- 输出内容寻址的 `evidence-pack-v2`，保留逐 Unit 命中原因、排序细节、未覆盖维度和降级诊断。
- PostgreSQL 17 + pgvector 0.8.5 的迁移、不可变索引发布、完整 round-trip 校验和原子 alias
  切换已在 Docker 中验证。默认向量模型为
  `jinaai/jina-embeddings-v2-base-code`，768 维；运行时可显式选择 CPU 或 CUDA，CUDA
  不可用或发生静默 CPU 回退时会 fail-closed。

Retrieval runtime 可以从 PostgreSQL 装载并完整校验不可变 `KnowledgeIndex`；
`RetrievalService` 接收调用方注入的索引，在进程内执行精确匹配和 cosine 检索。数据库已经建立
GIN、`pg_trgm` 和 768 维 HNSW 索引，但尚未把候选查询下推到数据库。这一实现适合第一批
50～100 条 Clause，并优先保证行为可重放。

`retrieval-request-v2` 的独立 closed schema、strict loader、code-first vector-query renderer 和
确定性 structural Builder 已实现。Builder 先重建完整 v1 request，再校验重建的
Analysis Card 与调用方提供的 ModelView/Request/Outcome/Result/Hybrid 引用图，只把 valid
AI positive 投影到独立 `ai_inferred_tags`；`not_supported/abstain` 不删除 static 信号。
V2 类型不继承 v1 request，当前 `RetrievalService` 也不接受它，因此这只是结构合同与
Builder 边界，没有改变任何 v1 检索结果。现有 non-formal live Campaign summary 和
`AITagResponseValidation` 不是 Builder 所需的 Hybrid 结构输入，不能直接喂给 V2。
声称已经 attempt 的 Hybrid outcome 还必须使用 provider-egress Card policy；这只保证结构一致，
不等于运行器身份或 provider provenance 已获证明。

`retrieval-request-v3` 的独立 closed schema、strict loader、code-first vector renderer 和受信 Builder
gate 也已实现。V3 不把 standalone Result/Outcome/Hybrid self-hash 当作执行证明：
`TrustedRetrievalRequestV3Builder` 必须持有部署配置的 Formal V2 verifier，并为每个 primary Unit
接收完整 formal evidence。它重建 v1 baseline 与 provider-egress Card，验证 raw-response/run graph、
Ed25519 Subject attestation 和外部 pinned runner registry 后，才把 verified positive 投影到
`ai_inferred_tags`。返回值是不可序列化的 `VerifiedRetrievalRequestV3`；序列化 V3 JSON 本身只是一份
结构产物，不是执行 authority。wrapper 另持有可信 V3 exact-facts 快照，并在每次访问时重验包括
`import_uses` 在内的完整 exact facts。

标准 `RetrievalService` 仍只接受 v1。Phase C 另行实现了 `RetrievalShadowServiceV3`，其
`compare(...)` 使用 exact-type gate，只接受 `VerifiedRetrievalRequestV3`；裸 V3、反序列化 JSON 或
子类都不能执行。该服务保留标准 v1 control `EvidencePack`，同时在独立
`retrieval-shadow-policy-v1` 下构造 `static_vector/hybrid` 两条 shadow arm。其输出不是新的正式
EvidencePack，而是 `retrieval-shadow-result-v1` 审计 artifact 和不可序列化的
`VerifiedRetrievalShadowResultV3` runtime wrapper。

上述 Formal/Phase C 合同测试不表示部署 key provisioning、KMS/HSM、真实 runner signer、provider
signature、source Git provenance、生产知识或真实 Retrieval 质量已经具备。2026-07-20 固定 synthetic
live 没有保存 raw bytes、完整 evidence graph 或当时 attestation，不能被新代码追认为 Formal V2/V3
输入，也没有产生 Phase C shadow 质量证据。

仓库当前没有真实、经人工策展的 `PublishedKnowledgeBuild` 文件，因此“生产知识已发布”仍不
成立。本轮 Docker 验证使用严格合法的最小 publication fixture；Golden 使用单独标记且必须
显式 opt-in 的 `golden_fixture`，两者都不会冒充真实知识资产。

## 3. 输入契约

当前可执行的正式请求是 `retrieval-request-v1`，由 Query Planner 从已校验的上游图构造。
每个 Unit 的关键字段为：

```jsonc
{
  "unit_id": "PhotoWall.ets@method:PhotoWall.loadImages:L14-L20",
  "source_ref_id": "code-source:sha256:...",
  "profile_id": "feature-profile:sha256:...",
  "review_question_ids": ["RQ-concurrency", "RQ-correctness", "RQ-navigation", "RQ-resource"],
  "dispatchable_review_question_ids": ["RQ-concurrency", "RQ-correctness", "RQ-navigation", "RQ-resource"],
  "exact_signals": {
    "components": [],
    "apis": ["setInterval", "router.pushUrl"],
    "decorators": [],
    "attributes": [],
    "syntax": ["async_fn", "arrow_fn"]
  },
  "exact_tags": ["has_async", "has_navigation", "has_timer"],
  "routing_tags": [],
  "retrieval_dimension_ids": ["DIM-06", "DIM-07"],
  "routing_dimension_ids": ["DIM-06", "DIM-07"],
  "semantic_code_excerpt": "this.timerId = setInterval(...)",
  "intent_summary": "组件异步加载数据并创建周期定时器",
  "quality": {
    "parser_layer": "L1",
    "context_degraded": false
  },
  "knowledge_token_budget": 1000
}
```

顶层请求另外绑定 `request_id/context_plan_id/feature_routing_id/feature_config_version`、解析后的
`index_version`、目标平台和总知识预算。Unit 必须稳定排序且为 1～50 个；Unit 预算之和必须精确
等于总预算。loader 拒绝重复 JSON key、未知字段、哈希漂移、未注册 Tag/Dimension/Question、
禁用检索维度以及上游 identity 不一致。

V2 在上述 v1 字段和不变量之上增加 `hybrid_analysis_id`、`import_uses`、
`ai_inferred_tags`、`tag_disagreements`、`candidate_dimension_ids` 和
`vector_query_policy`。`candidate_dimension_ids` 只是由 AI-positive Tag 重建的诊断字段，
不能绑定专项 Review Question，不能写入 formal Dimension coverage。V2 vector renderer 仅组合
代码摘录和 exact facts，不把 static/AI Tag、Dimension、RQ 或 intent 文本拼入查询。
该 renderer 目前未接入 Vector Retriever。

V3 继续保留 v1 正式字段和 V2 的信号分层，同时绑定
`formal_hybrid_analysis_id/formal_execution_outcome_id/formal_ai_result_id`、
`trusted_execution_subject_id/trusted_runner_attestation_id` 与
`ai_signal_scope=attestation_bound_requires_runtime_verified_wrapper`。只有完整 Formal V2 evidence 经
pinned-registry verifier 产生 opaque eligibility 后，Builder 才能构造 verified wrapper；strict loader
加载出的普通 `RetrievalRequestV3` 不能自行获得该 wrapper。verifier、eligibility、Builder 与 wrapper
拒绝普通属性替换或删除，wrapper 在构造和每次公开消费时重新核对 proof identities、AI positives、
disagreements、candidate Dimensions，以及全部 v1 baseline 字段（exact/routing Tag、RQ、正式
Dimension、规则、代码摘录、intent、quality 和预算）。AI positive 只进入
`ai_inferred_tags/candidate_dimension_ids`，AI negative 不删除 v1 static 信号，也不改变 formal RQ 或
Dimension coverage。signed `unavailable/invalid_output` 没有 `formal_ai_result_id`，因此 AI 信号字段
必须为空。

Phase C 的执行 policy 是独立 closed schema `retrieval-shadow-policy-v1`。它绑定当前 v1 Retrieval
config fingerprint、`rrf_k/result_limit`、code-first vector policy、formal-Dimension-only budget，并
要求以下五个 pool 以固定顺序完整存在：

```text
formal_exact / file_hint / text_keyword / ai_inferred / semantic_vector
```

五个 pool 分别保留候选上限、scope、pool rank 和 RRF weight。默认权重为
`1.0 / 0.5 / 0.25 / 0.25 / 1.0`；这些是冻结的 shadow 参数，不是真实语料校准结论。

## 4. Dimensions 在检索中的作用

Dimensions 不是唯一查询键，主要用于：

```text
对相关条款加权
控制各检查方向的 Evidence 覆盖
标记 uncovered dimension
```

不允许仅用 `DIM-04 可维护性` 查询整个知识库。

`always_check=true` 只代表 Prompt 始终检查；`retrieval_policy=signal_required` 时没有信号不检索文档。

## 5. 当前 v1 与 Phase C shadow 执行架构

```text
RetrievalRequest (v1)
        |
        v
Query Planner
为每个 Unit 构造结构化和语义查询
        |
        v
Formal Signal Planner
只消费 unit_exact、弱 file_hint 和代码摘录
        |
        +--------------------+
        |                    |
        v                    v
Exact Retriever        Vector Retriever
API catalog/组件/Tag    curated clause scenario embedding
        |                    |
        +---------+----------+
                  |
                  v
              RRF Fusion
                  |
                  v
       Applicability / Authority order
                  |
                  v
          Evidence Assembler
      Unit 分组、去重、覆盖优先、预算裁剪
                  |
                  v
             Evidence Pack
```

Query Planner、检索和 Evidence 组装均按 `unit_id` 独立执行。Unit 输出和 Clause 输出使用稳定
排序；请求、索引和 Evidence Pack 都由完整内容计算 SHA-256 identity。

该正式 v1 链保持不变。独立 Phase C 链为：

```text
VerifiedRetrievalRequestV3（exact type）
        |
        +-> v1 baseline -> RetrievalService -> control evidence-pack-v2
        |
        +-> formal_exact ───────┐
            file_hint ──────────┤
            text_keyword ───────┼-> shared candidate ledger
            ai_inferred ────────┤
            semantic_vector ────┘
                    |
                    +-> static_vector weighted RRF（不消费 ai_inferred）
                    +-> hybrid weighted RRF（消费全部五个 pool）
                    |
                    v
          retrieval-shadow-result-v1
          + VerifiedRetrievalShadowResultV3
```

两条 arm 复用同一份 pool rank/weight 账本，只以是否消费 `ai_inferred` 形成可归因对照。structured
keyword 固定为 `text_keyword` scope，AI positive 固定为 `ai_inferred` scope；它们均不能成为
`unit_exact`。AI not-supported/negative 不过滤正式候选。向量 query 使用 V3 code-first renderer，只消费
代码摘录与 exact facts，不拼入 Tag、Dimension、RQ 或 attestation。candidate Dimensions 仅存入
`diagnostic_only` 字段，两条 arm 的 coverage 和预算只使用正式 retrieval Dimensions。

## 6. Query Planner

生成两类查询：

### 结构化查询

```text
requested rule IDs
canonical APIs / aliases
components / decorators
exact Tags / 较弱的 routing Tags
Clause annotation keywords 与 intent/code excerpt 的确定性子串匹配
Dimensions 只用于加权和覆盖；publication/golden status 在建索引时强制为 Baselined
```

attributes、symbols、syntax、calls 和 resource references 当前进入确定性 intent/embedding 文本，
不作为独立 exact key；当前匹配器也不按 `func_ids` 检索。

### 语义查询

使用确定性模板优先：

```text
L18: this.timerId = setInterval(...)
apis: setInterval
code features: 使用了定时器 API
review focus: 检查资源与生命周期管理
```

当前不调用 LLM 生成查询。`intent_summary` 和 embedding 文本由 Unit kind/symbol、正式事实、
Tag 描述、专项 Review Question 以及 changed line 前后一行的最小代码摘录确定性构造。Dimension
只用于策略、加权和覆盖，不直接进入 embedding，避免抽象标题造成无关召回。

## 7. 路由边界

当前不再维护第二套动态 Domain Router。正式可检索 Dimension 和 Review Question 由 Feature
Routing 决定；Clause Domain 作为 Evidence 元数据保留，当前不参与召回或排序。没有 exact
signal 时，代码摘录仍可进行语义召回，但 Dimension 本身不能单独召回知识。

功能域仍表示“知识讲什么”，Dimension 表示“从什么角度评审”，两者不能混为同一分类；
`file_hints` 只能作为较弱的 routing 命中，不能成为最终 Finding 的代码证据。

## 8. 精确召回

优先匹配：

```text
canonical API
组件名
装饰器
Tag
raw_keywords
规则 ID
```

canonical API/alias 来自 `PublishedKnowledgeBuild.api_symbols`。Retrieval 使用该表规范化 Parser
提供的 API 名称，但当前不声称 Parser 与 API catalog 共用同一版本。如果请求带 API level，
Clause applicability 会被过滤；API declaration 自身的 availability 当前不参与检索过滤。

当前精确匹配器：

```text
rule ID / API alias / component / decorator / exact Tag
较低权重的 routing Tag
Clause annotation 的 raw/LLM keyword 与 intent/code excerpt 的子串匹配
目标平台、API level、language mode、permission、capability 适用性过滤
```

精确命中可解释，冻结权重顺序为 rule ID > API > component/decorator > exact Tag >
routing Tag > keyword。数据库相应 GIN/`pg_trgm` 索引已经存在，候选下推属于规模化优化，
当前小规模索引由进程内匹配器执行。

## 9. Embedding 召回

适合：

```text
组件拆分
可维护性
错误处理
多设备适配
没有共同标识符的语义场景
```

不适合单独处理：

```text
明确 API 规则
精确禁用语法
只嵌入 Dimension 标题
```

query embedding 使用确定性的代码优先查询文本，clause embedding 使用：

```text
scenario + heading_path + applicability + clause text
```

本地模型由 12 个 hybrid Golden case 做候选评估后选择。默认 Jina code 模型的实测结果为
Recall@5 `0.857143`、Precision@5 `0.692308`、MRR `0.875`、forbidden hit `0`；被比较的
`BAAI/bge-small-zh-v1.5` 分别为 `0.571429 / 0.269231 / 0.538194 / 1`，未通过门禁。
这组数字是当前 12-case 小样本的模型选型证据，不代表生产代码评审准确率。

## 10. 融合与重排

第一版使用 RRF，避免直接混合不可比较的关键词分数和向量分数：

```text
score = sum(1 / (rrf_k + rank))
```

后处理信号：

```text
production/golden 只允许 Baselined；evaluation fixture 可显式使用 Draft 且固定非生产
适用版本的内部规范/语言规格/安全公告/feature spec/官方文档权威度
API exact match
Tag match
Dimension overlap
Parser/context quality（diagnostic only）
```

其中 Parser/context quality 当前只进入结构化 diagnostic，不改变候选分数或排序。

Reranker 不是第一版必需项。只有 Golden Set 显示融合后排序仍明显不足时才加入。

## 11. Evidence 组装

目标：返回足够判断的最少条款，而不是文档越多越好。

步骤：

1. 按 `unit_id` 分组。
2. 同一 rule 去重。
3. 把 Knowledge Build 提供的 `parent_context` 复制进 Evidence；索引保留
   `neighbor_rule_ids`，但 v1 EvidencePack 尚未输出或扩展邻接条款。
4. 按正式 retrieval Dimension 优先保留覆盖。
5. 每个有真实信号的 Dimension 保留必要覆盖。
6. 按 authority、相关性和 token 裁剪。
7. 保存未覆盖维度和检索 trace。

## 12. 输出 EvidencePack

```jsonc
{
  "schema_version": "evidence-pack-v2",
  "evidence_pack_id": "evidence-pack:sha256:...",
  "request_id": "retrieval-request:sha256:...",
  "retrieval_version": "retrieval-v1",
  "retrieval_config_fingerprint": "retrieval-config:sha256:...",
  "index_version": "knowledge-index:sha256:...",
  "index_origin": "publication",
  "knowledge_build_id": "published-knowledge:sha256:...",
  "production_eligible": true,
  "source_bundle_id": "source-bundle:sha256:...",
  "embedding_version": "fastembed:0.8.0:jinaai/jina-embeddings-v2-base-code:sha256:...",
  "degraded": false,
  "units": [
    {
      "unit_id": "...",
      "profile_id": "feature-profile:sha256:...",
      "requested_dimension_ids": ["DIM-06", "DIM-07"],
      "routing_dimension_ids": ["DIM-06", "DIM-07"],
      "covered_dimension_ids": ["DIM-06"],
      "uncovered_dimension_ids": ["DIM-07"],
      "clauses": [
        {
          "rank": 1,
          "rule_id": "RESOURCE/TIMER/R-01",
          "rule_type": "constraint",
          "status": "Baselined",
          "text": "组件创建的定时器应在不再使用时主动清理。",
          "heading_path": ["资源管理", "定时器"],
          "parent_context": "组件资源应与生命周期配对。",
          "dimension_ids": ["DIM-06"],
          "tags": ["has_timer"],
          "apis": ["setInterval"],
          "components": [],
          "decorators": [],
          "domains": ["timer-subscription-lifecycle"],
          "source_ref": {
            "source_id": "arkui-specs",
            "revision": "98bbe6578e0f...",
            "relative_path": "timer/Feat-01-spec.md",
            "anchor": "L40-L47",
            "authority": "feature_spec",
            "content_hash": "sha256:..."
          },
          "matched_by": [
            {"kind": "api", "value": "setInterval", "scope": "unit_exact"},
            {"kind": "tag", "value": "has_timer", "scope": "unit_exact"}
          ],
          "applicability": "applicable",
          "score": 0.0325,
          "rank_detail": {
            "exact_rank": 1,
            "vector_rank": 1,
            "exact_score": 85,
            "vector_similarity": 0.81,
            "rrf_score": 0.0325,
            "authority_priority": 80,
            "dimension_overlap": 2
          },
          "token_count": 24
        }
      ],
      "diagnostics": []
    }
  ],
  "diagnostics": []
}
```

进入 Prompt：

```text
rule_id / text / status / source label / dimension_ids
```

只进入审计记录：

```text
matched_by / applicability / score / rank_detail / source_ref.anchor / diagnostics
```

`source_id/revision/rule_id` 必须进入 Prompt allowlist 和 ReviewReport 审计信息。原始
Markdown、Skill、XTS 或工具源码不能在在线请求时临时加入 Evidence Pack。

### 12.1 Phase C shadow 输出不是 EvidencePack

Phase C 的实验 arm 不定义新的 `evidence-pack-v3`，也不冒充现有 `evidence-pack-v2`。
`RetrievalShadowServiceV3` 返回的 runtime wrapper 内保留一份标准 v1 control
`evidence-pack-v2`，实验结果另存为 closed、内容寻址的
`retrieval-shadow-result-v1`。该 artifact 绑定 V3/control request、control EvidencePack、base config、
shadow policy、KnowledgeIndex/build/source bundle、embedding、Formal attestation 和逐 Unit 五 pool/
两 arm，并固定：

```text
execution_mode=shadow
formal_use_scope=hybrid_retrieval_shadow_only
authority_status=serialized_audit_only
evidence_qualification_status=not_qualified
production_qualified=false
user_visible=false
prompt_eligible=false
finding_evidence_eligible=false
downstream_use=audit_and_blind_evaluation_only
```

可序列化 artifact 的 self-hash 只证明内容 identity。只有服务私有构造的不可序列化
`VerifiedRetrievalShadowResultV3` 表示同一进程中的 `runtime_verified` binding；它每次公开访问都会重验
V3 authority、v1 control、构造时快照的 KnowledgeIndex/base config/shadow policy 和逐 Unit
Formal/Tag/Dimension/pool/arm identities。加载 shadow JSON 不会恢复
该 wrapper，也不能把结果发送给 Prompt、Finding Validator 或用户可见输出。
每次重验还会重建 structured pools，并把 ranked Clause 全字段回绑构造时快照的 KnowledgeIndex。
与 Formal V2 相同，这属于进程内普通 API fail-closed，不是能抵抗受信 Python 进程同时反射替换全部
内存 roots 的安全沙箱。

publication origin 的合法 KnowledgeIndex 仍只改变 index/build provenance 和 Clause 的 Baselined
约束，不会改变上述固定资格字段。当前仓库没有生产 PublishedKnowledgeBuild，也没有独立文档 Truth 或
真实 Phase C P/R；因此 fixture、evaluation index 或 publication index 上运行成功都不能写成生产
qualified。

## 13. 当前接口和数据库边界

```python
request = build_retrieval_request(analysis_result, context_plan, ...)
evidence = RetrievalService(index, embedding_provider=provider).retrieve(request)

# Phase C 使用 TrustedRetrievalRequestV3Builder 返回的不可序列化 authority；
# compare 的 control_evidence_pack 仍是上面的 evidence-pack-v2。
shadow = RetrievalShadowServiceV3(index, embedding_provider=provider).compare(
    verified_request_v3
)

store.publish(index)                 # 不可变且幂等
store.load(index_version)            # 重建后逐字段校验
store.switch_alias(index_version)    # 仅允许 ready 索引
store.resolve_alias("current")
```

运行命令为 `arkts-retrieval`，也可使用 `tools/manage_retrieval.py`；迁移命令为
`tools/migrate_retrieval.py`。Docker 定义位于 `compose.retrieval.yaml`，迁移位于
`migrations/retrieval/`。第一版唯一持久化后端是 PostgreSQL + pgvector，不维护
SQLite/Faiss 双栈。

## 14. 缓存（尚未实现）

```text
cache key =
hash(UnitQuery)
+ index_version
+ routing_version
+ retrieval_version
+ embedding_version
```

这是后续服务化的目标 key；当前没有在线 Evidence 缓存。内容寻址的 request/index/config/
embedding identity 已经具备，因此以后加入缓存无需改变正式结果合同。

## 15. 无结果和降级

| 场景 | 行为 |
|---|---|
| 精确无结果、向量有结果 | 返回向量结果并标明 matched_by |
| 两路都无结果 | 返回空 clauses 和 uncovered_dimensions |
| Embedding 服务失败 | 精确检索继续，记录 degraded |
| 发布或装载索引时 DB 不可用 | publish/load 失败；不得伪造 Evidence；已注入索引的进程内检索不访问 DB |
| Parser/context degraded | 继续检索并记录 `parser_degraded`，下游必须限制强结论 |

无 Evidence 时，Final LLM 只能给受限 suggestion，不能声称违反具体规范。

## 16. 配置

见 [配置与版本规范](../architecture/configuration.md)：

```text
config/retrieval.yaml
config/dimensions.yaml
```

环境：

```text
ARKTS_RETRIEVAL_DATABASE_URL（或 DATABASE_URL）
ARKTS_FASTEMBED_CACHE
```

模型 ID、维度、`--local-files-only`、`--embedding-device`、`--embedding-batch-size` 和
`--embedding-threads` 均可通过 CLI 显式传入。默认批量为 8、线程数为 2；配置 loader
拒绝重复 YAML key、未知字段、越界值、重复 authority 和破坏精确匹配优先级的权重。

## 17. 技术栈

| 层 | 技术 |
|---|---|
| 主体 | Python 3.12 |
| 模型 | Pydantic v2 |
| DB | PostgreSQL |
| 精确/模糊 | GIN + pg_trgm（schema 已准备；候选下推未实现） |
| 向量 | pgvector HNSW（schema 已准备；候选下推未实现） |
| DB 访问 | psycopg 3 + pgvector adapter |
| Embedding | FastEmbed 0.8，Jina code 768D，CPU/CUDA 显式选择 |
| 配置 | ruamel.yaml + Pydantic strict loader |
| 测试 | pytest + Docker 真实 PostgreSQL 契约测试 |

## 18. 质量指标

```text
Recall@5
Precision@5
MRR
empty result rate
wrong domain route rate
irrelevant clauses per Unit
Dimension evidence coverage
p50/p95 latency
```

当前 Golden Set 形式：

```text
ReviewUnit Query
-> 应命中的 rule_id 集合
-> 可接受但非必需 rule_id
-> 明确不应命中的 rule_id
```

`tests/golden/retrieval/` 有 36 个自包含、人工 expected case：18 exact、12 hybrid、6
embedding failure，共 39 个 Unit，其中 8 个 true negative。确定性 fixture 的 strict baseline
与 `--require-perfect` 当前为 36/36、Recall@5 `1.0`、Precision@5 `1.0`、MRR `1.0`、
forbidden hit `0`。这证明合同、排序、降级和 fixture 真值一致，不是现实世界模型准确率；真实
embedding 质量单独使用上一节的 12-case 指标报告。`tools/run_retrieval_e2e.py` 另外验证
ReviewUnit 到 EvidencePack 的正式图，但使用一条 test-only `golden_fixture`，不是 Jina +
PostgreSQL + 真实 Knowledge publication 的单进程全链路；Jina 与数据库 round-trip 由独立集成测试覆盖。

## 19. v1 与 Phase C 交付边界

```text
已完成：正式 Query/Evidence 合同、精确 + 向量召回、RRF、适用性、预算、Golden、
        本地 embedding、PostgreSQL fixture publication/alias、fixture-backed ReviewUnit 到 EvidencePack E2E
Phase C：V3 exact-type authority、独立 shadow policy、五来源 pool、static_vector/hybrid 加权 RRF、
        code-first vector、v1 control EvidencePack、shadow audit artifact 与不可序列化 runtime wrapper
外部前置：生成真实且经人工策展的 PublishedKnowledgeBuild，再发布首个生产索引
规模优化：当 Clause 数量显著超过首批范围时，把 GIN/HNSW 候选查询下推到 PostgreSQL
质量优化：扩充真实代码/真实 Clause Golden；只有数据证明必要时才引入 reranker
下游模块：Rules/Prompt/Final LLM 只能消费正式 v1 EvidencePack；Phase C artifact 不可消费且 Retrieval
          不直接输出 Finding
```
