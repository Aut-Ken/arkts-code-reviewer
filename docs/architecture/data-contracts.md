---
title: 跨模块数据契约
status: canonical
updated: 2026-07-23
---

# 跨模块数据契约

## 1. 目的

本文定义模块之间交换什么数据、字段由谁拥有，以及当前模型向目标模型的演进方向。
模块内部实现可以变化，但跨模块语义必须通过这里对齐。

## 2. 契约原则

1. 文件路径统一使用 `/`。
2. 所有源码行号对外使用 1-based 文件绝对行号。
3. Unit 内相对行号只用于调试，不作为最终 Finding 主坐标。
4. 一条数据必须能追溯到源码 revision、配置版本和知识索引版本。
5. Parser 产物只描述事实，不包含质量结论。
6. Tags 和 Dimensions 只描述场景与检查方向，不是 Finding。
7. Evidence 必须带稳定 `rule_id` 和来源锚点。
8. Final LLM 只能引用本次 Evidence Pack 或 Rules 中存在的 ID。
9. 当前已实现字段和目标字段必须明确区分，不能把设计稿当成运行时事实。
10. 任何外部资料都必须通过 `source_id + revision + relative_path` 定位，branch 不能作为复现依据。
11. 原始 Skills、代码语料和工具源码不能直接转换为在线 Evidence。

## 3. 数据所有权

| 数据 | 生产模块 | 主要消费模块 |
|---|---|---|
| `SourceRecord/SourceBundle` | Source Registry | Knowledge Build、Evaluation、Audit |
| `NormalizedDocument/SourceRef` | Source Adapter | Clause Parser、Metadata Extractor |
| `MarkdownDocumentMap` | Document-First Structure Builder | Document Card Builder、Document Catalog、Audit |
| `SourceAtomSet` | Document Projection Structure Builder | L1/L2 Mapping、Projection Compiler、Audit |
| `SourceFragmentSet` | Document Semantic Structure Builder | Semantic Facet Builder、Audit；固定非 Evidence |
| `SemanticFacetSet/SemanticRelationGraph` | Document Semantic Projection Builder | L2 Shadow adapter、未来 DeepSeek/Grok runner、Audit；固定未语义审核 |
| `DocumentCardRequest/DocumentCardDispatchPlan` | Document Card Builder | 受控 DeepSeek runner、Audit；Plan 不是执行授权 |
| `DocumentCard/DocumentCatalogBuild` | Document-First Navigation | 文档导航、Audit；固定非 Evidence、非生产资格 |
| `DocumentCardCampaignInspection` | Document Card Campaign Inspector | 外发审批、Audit；inspect-only，不代表已经执行 |
| `DocumentProjectionMapping/Manifest/Record` | Document Projection Builder | 独立 L2 Shadow Store、Audit；固定非 Evidence、未语义审核 |
| `KnowledgeClause/ApiSymbolCatalog` | Knowledge Build | Retrieval、Rules、Parser canonicalization |
| `ChangeSet` | Input | Parser、ReviewUnit、Output |
| `CodeFacts/FileAnalysis` | Parser | ReviewUnit、Feature Routing、Rules |
| `ReviewUnit/UnitFactScope` | ReviewUnit | Feature Routing、Context Planner、Evaluation |
| `UnitFeatureProfile/FeatureRoutingResult` | Feature Routing | Context Planner、Retrieval、Rules、Prompt、Evaluation |
| `AITagShadowUnitEvaluation/AITagShadowEvaluationReport` | Hybrid Analysis Evaluation | Evaluation、Audit；禁止作为 Feature Routing/Hybrid/Retrieval 输入 |
| `TagTruthSelection/ReviewPacket/ReviewReceipt/Consensus/Publication` | Evaluation Governance | Tag candidate evaluation、Audit |
| `NearDuplicatePairSelection/Consensus/CalibrationReport/PolicyApprovalReceipt` | Evaluation Governance | Near-duplicate policy calibration、Tag Truth release review、Audit |
| `RetrievalRequestV3/VerifiedRetrievalRequestV3` | Retrieval Query Planner V3 | Retrieval Shadow；只有不可序列化 wrapper 是运行 authority |
| `RetrievalShadowResultV3/VerifiedRetrievalShadowResultV3` | Retrieval Shadow | Evaluation、Audit；禁止进入 Prompt、Finding 或用户可见 Evidence |
| `RetrievalDocumentTruthV1` | Evaluation 标注合同 | Phase D0 scorer、Audit；调用方提供的 self-hash 标签不等于人工双审或 sealed Truth |
| `RetrievalShadowEvaluationReportV1` | Retrieval Evaluation | Evaluation、Audit；固定 offline/not-qualified，禁止进入 Prompt、Finding |
| `EvidencePack` | Retrieval | Prompt、Finding Validator、Evaluation |
| `RuleFinding` | Rules | Prompt、Output、Evaluation |
| `ReviewRequest` | Prompt Builder | Final LLM |
| `Finding` | Final LLM + Validator | Output、Evaluation |
| `ReviewReport` | Output | GitCode、人工审核、Evaluation |

### 3.1 当前 SourceRecord

`/home/autken/Code/arkts-knowledge/registry/sources.yaml` 已登记 19 个本地仓库。主项目已实现
严格 `SourceRegistry` loader、来源路径环境变量覆盖、Git checkout 验证和稳定
`SourceBundle`：

```yaml
- id: openharmony-docs
  group: knowledge_source
  kind: official_documentation
  local_path: /home/autken/Code/arkts-knowledge/sources/official-docs/openharmony-docs
  env_override: OPENHARMONY_DOCS_PATH
  branch: master
  revision: c8f5fb6c2fe03cf66b8a41c196ad7fc5e7891c47
  checkout:
    mode: sparse
    include: [...]
  ingestion:
    include: [...]
    exclude: [...]
    execute_repository_scripts: false
    index_as_normative_knowledge: true
  governance:
    authority: official_documentation
    curation_required: true
    raw_prompt_use_allowed: true
```

`load_source_registry()` 读取显式绝对路径，或在未显式传入时读取
`ARKTS_SOURCE_REGISTRY`，再回退到默认 Registry。`resolve_source_path()` 按
`SourceRecord.env_override` 对应环境变量优先、`SourceRecord.local_path` 次之解析本地路径；
覆盖值必须是绝对路径。`build_source_bundle(..., verify=True)` 还会校验 Git toplevel、remote、
branch 和 `HEAD` 与 Registry 的固定 revision 一致，并计算：

```jsonc
{
  "source_bundle_id": "sha256:...",
  "sources": [
    {
      "source_id": "openharmony-docs",
      "revision": "c8f5fb6c...",
      "ingestion_profile_hash": "sha256:..."
    }
  ]
}
```

### 3.2 SourceRef

Clause、API 元数据、候选规则和 Golden Case 共用：

```jsonc
{
  "source_id": "openharmony-docs",
  "revision": "c8f5fb6c...",
  "relative_path": "zh-cn/application-dev/.../example.md",
  "anchor": "heading-or-lines",
  "authority": "official_documentation",
  "content_hash": "sha256:..."
}
```

`relative_path` 必须相对于登记的仓库根目录，禁止保存依赖当前机器的绝对文件路径。

### 3.3 NormalizedDocument 与 ApiSymbolCatalog

Source Adapter 的统一文档输出：

```jsonc
{
  "document_id": "openharmony-docs:zh-cn/.../example.md",
  "source_ref": {},
  "media_type": "text/markdown",
  "title": "...",
  "heading_tree": [],
  "body": "...",
  "metadata": {
    "language": "zh-CN",
    "api_level": null,
    "release": null
  }
}
```

`interface-sdk-js` 不以普通文档段落为主要产物，而应生成结构化 API 条目：

```jsonc
{
  "canonical_name": "image.createPixelMap",
  "aliases": ["img.createPixelMap"],
  "since": 9,
  "deprecated_since": null,
  "permissions": [],
  "system_capabilities": [],
  "source_ref": {}
}
```

### 3.4 Document-First 文档导航合同

当前 Document-First 切片与 Clause publication 并行存在。它先为固定 Markdown 建立可重放的文档
结构与导航摘要，但不把摘要提升为规范证据：

```text
NormalizedDocument
-> MarkdownDocumentMap
-> DocumentCardRequest
-> DocumentCardDispatchPlan
-> raw provider response
-> DocumentCardDraft
-> DocumentCard
-> DocumentCatalogBuild
```

#### 3.4.1 MarkdownDocumentMap

`markdown-document-map-v1` 通常由静态 `markdown-document-structure-v1` builder 生成；带 YAML
front matter 的输入使用兼容 schema 的 `markdown-document-structure-v2-front-matter`。它保存：

```text
document_id / SourceRef / normalized_body_hash
title / language / release / api_level / language_mode
按正文顺序排列的 section_id / ordinal / kind / heading_path
heading/content/subtree 的 1-based SourceSpan
每节 direct content 与 subtree 的 SHA-256
map_id = 完整规范化内容的 content-addressed identity
```

Map 不调用模型；严格 verifier 会从同一 `NormalizedDocument` 重建并逐字段比较。

#### 3.4.2 DocumentCardRequest 与 DispatchPlan

`document-card-request-v1` 将完整 Markdown、Map、固定 `deepseek-document-card-v1` Prompt、模型和
export-policy fingerprint 绑定到 `request_id`。`document-card-dispatch-plan-v1` 再冻结最终
DeepSeek wire JSON、body hash/bytes、端点、单次 attempt、无重试、TLS、超时、响应体和最大输出
预算。Request 的 qualification 是 `navigation_generation_request_not_authorization`，Plan 的
qualification 是 `plan_not_authorization`；二者都不是外发批准。

真实发送还必须同时满足 Registry `raw_prompt_use_allowed=true`、独立精确
source/revision/path allowlist，以及调用方对精确 Plan ID、wire body SHA-256、最大输出 token 和
固定 acknowledgement 的批准。离线 inspect 不创建 credential provider，不读取 `.env`/API Key，
也不访问网络。transport 返回后，runner 会再次执行响应字节上限检查；若响应正文原样包含当前
API Key，则按安全的 transport failure 处理且不保留该正文。

#### 3.4.3 DocumentCard 与 DocumentCatalogBuild

Provider JSON 先严格解析为 `document-card-draft-v1`，再核对 document ID、所有 section ID 的
完整且唯一覆盖、来源、Map 和正文 hash，最后封装为 content-addressed `document-card-v1`。
Card 的摘要、主题、API 提示和逐节摘要都是导航字段，固定：

```jsonc
{
  "use_scope": "navigation_only_not_evidence",
  "evidence_eligible": false
}
```

`document-catalog-build-v1` 只接受能从可信 `NormalizedDocument + MarkdownDocumentMap +
DocumentCard` 完整复核的输入，按稳定来源身份排序并拒绝重复或错绑。Catalog 固定
`evidence_eligible=false`、`production_qualified=false` 和
`qualification=navigation_catalog_contract_not_quality_qualified`。当前只实现 Catalog 构建与
验证合同，没有实现多文档 Catalog Router、Catalog 到 Clause Retrieval 的运行连接，也没有改变
`KnowledgeIndex`、`RetrievalRequest` 或 `EvidencePack` 合同。

#### 3.4.4 当前真实运行与 Campaign 边界

`taskpool-vs-worker.md` 已在精确 Plan
`document-card-plan:sha256:c911311d787e72252d7134acc1393259663ba78d061315d1c2003342124e7768`
下执行一次真实 DeepSeek smoke。结果为 1 attempt、0 retry、`valid_card`，5/5 section summaries
通过完整重建，并生成 Card
`document-card:sha256:5a321b5fbacf1db8bd4ce941fb631ad8be8e4174182cc913941ba3008c1a16b5`
和一条目的 Catalog
`document-catalog:sha256:c3aaff27622a50fd271bf49b00bd5ce5613c11c939ca6ac13cba7dac32b9e5b5`。
`valid_card` 只证明结构和 provenance 合同。该 Card 的 `important_apis=[]`，而原文存在多个明确
API/类/装饰器样式词，因此内容质量仍为 `NOT QUALIFIED`。

另外 7 篇固定 Markdown 已生成 `document-card-campaign-inspection-v1`：

```text
campaign_id:
  document-card-campaign:sha256:1909052b40883a6d259024d8cc210d2c6a7852471f109fd680000c113f2d977c
plan_set_digest:
  document-card-campaign-plan-set:sha256:8b4b80e8eeff96b4010591d91c283e44b0bcc0c1cb5944f19e854fa79b63fe7a
caps:
  7 attempts / 163932 request bytes / 28672 output tokens /
  14000000 response bytes / 840000 ms aggregate timeout
state:
  credential_accessed=false
  network_attempted=false
  execution_authorized=false
```

`document-card-campaign-live-receipt-v1` 及其受控 runner 已实现。runner 要求调用方逐项精确批准
上述 Campaign、Plan-set、文档数和全部聚合预算，并提供固定确认语；在 credential 读取和第一次
请求前，它会重建 Campaign、逐字节复核 selection、inspection 和每个 Plan 的 `00`～`05`，确认
全部 Plan 均无 consumed marker 或 `06`～`09` 碰撞。随后按 canonical 顺序复用单文档 runner，
每个 Plan 只尝试一次、无重试；普通 transport、Provider 或 Card 失败保留 typed receipt 并继续，
完整性、replay 或总时限准入失败则停止。

每篇完成后按 `06 raw response -> 07 draft -> 08 card -> 09 receipt` 写入实际存在的产物，最后写
内容寻址的 Campaign receipt。receipt 绑定逐篇状态、receipt/card ID、延迟、token、响应字节和
Campaign elapsed，并固定 `evidence_eligible=false`、`production_qualified=false`。写入只保证单文件
原子和 receipt-last，不宣称多文件事务；中断后 marker 仍表示唯一尝试已经消费，不能重试。

2026-07-22 已对上述精确 Campaign 执行一次受控真实请求。7 个 Plan 各 attempt 一次、无重试，
全部为 `valid_card`；累计输入 45,992 tokens、输出 11,677 tokens、原始响应正文 42,194 bytes，
Provider latency 合计 134,503 ms，Campaign elapsed 为 134,834 ms。根回执为：

```text
document-card-campaign-live-receipt:sha256:49e7609094eac9639c64ba29e9598e66fc3c143a8cf61c21636c66bcd5b88411
```

7 组 raw response 均可重建为相同 Draft、Card 和单篇 receipt，根 receipt 也可由 7 组结果重封为
相同 identity。115 个静态 section 均有且只有一条对应摘要。该结果证明真实多文档执行、结构和
provenance 链成立；它没有人工 Truth 或路由 Recall/Precision。人工只读观察发现长文档会遗漏
数值限制、禁止项、例外条件和关键符号，因此内容质量仍未 qualified。当前也没有由 7 张 Card
构建并验证的多文档 Document Catalog 或任何 Catalog Router 结果。

#### 3.4.5 L1/L2 Document Projection 基础合同

当前已实现不依赖模型调用的第一段投影链：

```text
NormalizedDocument + MarkdownDocumentMap
-> SourceAtomSet
-> caller-provided DocumentProjectionMappingDraft
-> sealed DocumentProjectionMapping
-> deterministic L2 Markdown + ProjectionManifest
-> DocumentProjectionVerification
-> immutable DocumentProjectionRecord
-> isolated PostgreSQL document_projection schema
```

`SourceAtomSet` 在现有 section 外层内继续按 Markdown block 建立 Atom。普通文档继续使用
`markdown-document-structure-v1`；检测到 YAML front matter 时 Map 明确使用
`markdown-document-structure-v2-front-matter`，避免在不改变版本 identity 的情况下修正旧解析
行为。段落、顶层列表项及其嵌套内容、表格、代码块、引用块、Note 和无法识别的非空 block 都能
形成 Atom；YAML front matter、标题、空行、HTML 注释和分隔线进入 Region。Atom 与 Region 必须
无遗漏、无重叠地覆盖 L1 的全部物理行，Atom 只保存行号和正文哈希，可信正文必须从同一个
`NormalizedDocument` 切片重建。

Mapping 允许同一个 Atom 出现在多个分类 binding 中，但所有 eligible Atom 必须恰好属于
“至少一个 binding”或 `unclassified_atom_ids`，两者不能交叉。L2 Markdown 只在分类目录中建立
链接，canonical 原文单元库按 L1 顺序保存每个 Atom 正文一次；AI 生成的标题、主题和别名会经过
Markdown 转义，不能修改 L1 正文。

`DocumentProjectionRecord` 当前只达到机械资格：

```text
state = mechanically_verified
qualification = mechanically_verified_projection_not_semantically_reviewed
use_scope = retrieval_projection_only_not_evidence
evidence_eligible = false
production_qualified = false
```

PostgreSQL 使用独立 `document_projection` schema，保存不可变 projection version、Atom、Binding
及 Binding-to-Atom 多对多关系；migration history 使用 checksum、prefix 和 advisory lock 校验。
写入必须先处于 `building`，Atom/Binding 数量、分类/未分类互斥和每个 Binding 的非空引用全部满足
后，数据库才允许单向封存为 `mechanically_verified`；封存后禁止追加、修改或删除子行。Store 只对
同一个 projection identity 使用有超时的 advisory lock，不会让不同文档的读取和写入共用全局
排他锁。

Store 封存后会立即从完整 JSON 与规范化表两条路径重建并逐字段比较，内容不同的同 ID 重放、正文
漂移、分类漂移和 ordinal 漂移全部 fail-closed。严格 Record loader 同样执行完整 rebuild，不能把
调用方可自行重算的 self-hash 当成机械验证资格。当前真实 PostgreSQL round-trip 只证明
migration、写入、封存、读取、幂等和 sealed-after-insert 拒绝合同可执行；尚无 DeepSeek Mapping
Prompt、Grok Receipt、真实 L2 文档资产、Document Catalog/检索 runtime 或 Recall/Precision
证据。

#### 3.4.6 Atom 内部 Fragment 与 Semantic Facet Shadow 合同

为解决“一个 Markdown block Atom 内同时讨论多个组件、限制、例外或场景”的问题，当前新增了与
Projection v1 并行的 sidecar 合同，没有修改既有 `SourceAtomSet`、`DocumentProjectionMapping`
或 PostgreSQL `document_projection` identity：

```text
NormalizedDocument + MarkdownDocumentMap + SourceAtomSet v1
-> deterministic SourceFragmentSet v1
-> caller-provided SemanticFacetSetDraft v1
-> mechanically sealed SemanticFacetSet v1
-> caller-provided SemanticRelationGraphDraft v1
-> mechanically sealed SemanticRelationGraph v1
-> explicit lossy Projection v1 adapter (fail-closed when unrepresentable)
-> existing DocumentProjectionMapping v1 / L2 renderer
```

`SourceFragmentSet` 不是新的规范文本，也不是 AI 自由切片。Builder 对 paragraph、顶层 list item、
blockquote 和 note 使用固定的强句末符、换行和 800 codepoint 上限建立地址片段；table、code block
及其他无法安全细分的 Atom 保持 whole-Atom Fragment。每个 Fragment 只保存 parent Atom、Atom 内
UTF-8 byte 半开区间和正文哈希，不重复保存正文。每个 Atom 至少一个 Fragment；Fragment 必须从
byte 0 连续覆盖到 Atom 末尾，禁止空片段、gap、overlap、跨 Atom 和非法 UTF-8 边界。可信文本仍
必须从同一 L1 和 Atom 切片重建。

`SemanticFacetSetDraft` 是本地 reducer 的语义 payload 边界，当前只由调用方 fixture 提供。它不
是完整 DeepSeek Response/Receipt；未来模型响应还必须在外层绑定 FragmentSet、Request/Plan、
Prompt、model/policy 和 raw response hash。Draft JSON loader 要求包括空数组在内的全部字段显式
出现，不能把模型漏字段静默解释为默认值。一个
Facet 可以引用多个 primary Fragment、多个闭合 `CategoryKind`，同一个 Fragment 也可以进入多个
Facet。每个 Context Signature 显式区分 subject、component、role、scenario、operation、condition
和 version，并单独记录 `required_context_fragment_ids`；所有 Fragment 必须进入至少一个 Facet 或
`unclassified_fragment_ids`，required-context 引用不能冒充已分类覆盖。

`SemanticRelationGraph` 在 Facet identity 生成后独立构建，当前只允许同文档 typed edge：
`supplements`、`exception_of`、`prerequisite_for`、`example_of`、`alternative_to`、
`contrasts_with`、`apparent_conflict` 和 `same_subject_different_context`。关系端点必须存在，禁止
self-edge 和重复 canonical edge；对称关系会规范化端点顺序。Relation 只表达导航联系，不能自动
扩张 Evidence。

新合同的资格固定为：

```text
SourceFragmentSet.qualification = exact_source_partition_not_semantically_qualified
SemanticFacetSet.qualification = mechanically_verified_facets_not_semantically_reviewed
SemanticRelationGraph.qualification = mechanically_verified_relations_not_semantically_reviewed
evidence_eligible = false
production_qualified = false
```

Legacy adapter 可以在可表达时把现有 Atom-level Mapping 展开为多 Fragment Facet，也可以把 Facet
按 parent Atom 和每个 category 有损折叠回 Projection v1。它绝不改变旧 renderer/数据库 schema，
但不是任意新旧产物的无损双向转换：旧 Binding 的 subject 为空、同一 Atom 同时含 classified 与
explicitly-unclassified Fragment，或不同 Facet 折叠成相同 v1 Binding 时都会 fail-closed，而不是
注入标题、提升 unknown 或静默去重。Facet 的精确 Fragment 范围、扩展 Context 维度与 Relation
不会进入 v1，因此 canonical 审核仍必须针对原始 Facet/Relation 产物。当前尚无 DeepSeek Facet Prompt/Request/Plan/live response、Grok
Facet review、跨文档 Topic Graph 或真实 Facet 检索质量证据；这些 self-hash 和机械门禁不能证明
AI 分类正确。`SourceFragmentSet`、`SemanticFacetSet` 和 `SemanticRelationGraph` 的公开 sealed
loader 都要求同时提供可信上游并执行完整机械 verifier；只有 Draft loader 可以脱离上游解析。

## 4. 兼容 FileInput 入口

```python
class FileHunk:
    new_start: int
    new_lines: int

class FileInput:
    path: str
    content: str
    hunks: list[FileHunk]
```

当前 `path` 合同是仓库根目录相对的规范化逻辑路径。Analyzer 拒绝绝对路径、逃出根目录的
traversal，以及同一请求中规范化后重复的路径别名；CLI 负责把 cwd 内实际文件转换为这种
逻辑路径。这样 `unit_id` 不依赖本机 checkout 绝对位置。

兼容 `FileInput/FileHunk` 的限制：

- 只保存新文件范围，不保存精确 added/deleted lines。
- 不保存 base 版本和旧文件内容。
- Git hunk 中的上下文行可能被误当成改动行。
- 删除-only、rename 和 binary file 没有正式契约。

## 5. CodeSourceRef 与 ChangeSet v1

RU-3 已实现 `CodeSourceRef`，用于把 Parser/ReviewUnit 结果绑定到不可变源码快照。RU-4 已在
此基础上实现 `change-set-v1` / `change-normalizer-v1`、`ChangeAtom`、`ChangedFile` 和
`ChangeSet`。旧 `FileInput/FileHunk` 只作为兼容本地入口，不具备精确 diff 语义。

### 5.1 CodeSourceRef

| 字段 | 含义 |
|---|---|
| `source_ref_id` | 由 repository、revision、规范化 path 和 content hash 生成的稳定 ID |
| `repository` | 稳定仓库标识，不是本机绝对路径 |
| `revision` | 不可变 commit 或 snapshot ID；branch 不能用于复现 |
| `path` | 相对仓库根的规范化 POSIX 路径 |
| `content_hash` | 完整文件内容的 SHA-256 |

`CodeSourceRef` 只定位一个确定的源码快照。base/head 角色由引用它的
`ChangedFile/ChangeAtom` 表达，不进入 source identity。

### 5.2 ChangeAtom

| 字段 | 含义 |
|---|---|
| `atom_id` | 规范化 atom 内容与 `diff_normalizer_version` 的确定性 ID |
| `kind` | `addition \| deletion \| replacement` |
| `old_source_ref_id` / `new_source_ref_id` | 分别指向 base/head 源；addition 可无 old，deletion 可无 new |
| `old_span` / `new_span` | 各自 source 上的 1-based、end-inclusive 行范围；缺少一侧时为 `null` |
| `added_new_lines` | head source 上精确 added lines，升序且唯一 |
| `deleted_old_lines` | base source 上精确 deleted lines，升序且唯一 |
| `diff_positions` | 可选的 Git 发布坐标映射，不代替 source span |
| `diff_normalizer_version` | 生成 atom 的规范化器版本 |

### 5.3 ChangedFile

| 字段 | 含义 |
|---|---|
| `changed_file_id` | status、path、source ref、atom IDs 和 binary 标记的确定性 ID |
| `status` | `added \| modified \| deleted \| renamed` |
| `old_path` / `new_path` | base/head 的规范化 repository-relative path |
| `old_source_ref_id` / `new_source_ref_id` | 对应 base/head 快照；缺少一侧时为 `null` |
| `atom_ids` | 本文件的 ChangeAtom IDs；pure rename 可以为空 |
| `is_binary` | true 时不得伪造 source ref 或 atom |

### 5.4 ChangeSet

| 字段 | 含义 |
|---|---|
| `schema_version` | 当前固定为 `change-set-v1` |
| `change_set_id` | repository、base/head revision、规范化 files/atoms 和 normalizer version 的确定性 ID |
| `repository` | 变更所属的稳定仓库标识 |
| `base_revision` / `head_revision` | 本次分析的不可变端点 |
| `diff_normalizer_version` | 当前默认 `change-normalizer-v1`，并参与 atom/ChangeSet identity |
| `source_refs` | 本次变更引用的 `CodeSourceRef` 表，按 ID 稳定排序 |
| `files` | old/new path、`added \| modified \| deleted \| renamed` 和所属 `atom_ids` |
| `atoms` | 精确 `ChangeAtom` 表，按源角色、path、span 和 ID 稳定排序 |
| `diagnostics` | 当前冻结 `binary_source_unavailable`；其他非法结构直接 fail-closed |

纯 rename 可以没有 `ChangeAtom`；空文件仍有合法 `CodeSourceRef`；binary 变更不伪造行级
atom，而是进入 diagnostics。Git hunk context line 不得进入 `added_new_lines` 或
`deleted_old_lines`。当前 normalizer 消费调用方提供的结构化 source/span/changed lines；它
不解析 raw Git diff，也不负责获取仓库内容或连接 GitCode。

## 6. 当前 CodeFacts

当前 Parser 输出：

```python
class CodeFacts:
    path: str
    imports: list[ImportInfo]
    components: set[str]
    apis: set[str]
    decorators: set[str]
    attributes: set[str]
    symbols: set[str]
    syntax: set[str]
    declarations: list[Declaration]
    parser_layer: "L0" | "L1" | "parse_degraded"
    warnings: list[str]
```

Parser v1 对 ReviewUnit 的稳定保证是：文件级事实集合，以及 declaration occurrence 的
1-based inclusive 起止行。正式 Golden 不评分 `start_col/end_col`，所以当前对外定位不能
依赖列坐标。

具体边界：

- `Declaration.kind/name/qualified_name/span/parent_name/text` 可用于声明选择和源码切片。
- `qualified_name` 与 `parent_name` 都不是 occurrence 唯一 ID；持久 ID 必须包含 kind 和 span。
- 兼容 `CodeFacts` 的 components/symbols 是文件级并集；不能直接充当 Unit occurrence。
- 兼容 `CodeFacts` 的 APIs/decorators/attributes/syntax/imports 没有 Unit owner，只能作为
  `file_hints`；正式 FileAnalysis occurrence 另有 owner/span 合同。
- `parser_layer=L1` 表示 sidecar 成功，不表示没有 ERROR/missing node；warnings 必须传播。
- Parser fact 不是 Finding evidence，缺少某个 fact 也不能证明源码中一定不存在该事实。

## 7. 当前 FileAnalysis（RU-3）

RU-3 已通过独立 Parser v2 Golden 建立 occurrence-level facts，并对同一 `CodeSourceRef`
只解析完整文件一次。`unit_exact + file_hints` 是投影后的双作用域输出，不替代下列
occurrence/provenance 合同。

### 7.1 FileAnalysis

| 字段 | 含义 |
|---|---|
| `schema_version` / `analysis_id` | 结果 schema 版本，以及 source/parser 输入的确定性 ID |
| `source_ref` | 被解析的唯一 `CodeSourceRef` |
| `parser_version` | Parser 实现、sidecar 和 grammar 的可复现版本 |
| `parser_quality` | `layer`、`error_nodes`、`missing_nodes` 和结构化 `warnings` |
| `file_hints` | 与兼容 `CodeFacts` 一致的文件级存在信号；不属于任何 Unit |
| `declarations` | 带 `declaration_id`、span、UTF-16 exact range、`parent_id` 和 quality 的声明 occurrence |
| `review_regions` | 不是现有 declaration kind 的可评审区域 |
| `fact_occurrences` | 带 source span、owner 和 provenance 的事实 occurrence |
| `diagnostics` | 不可定位、owner 未解析或 Parser 降级等结构化诊断 |

### 7.2 FactOccurrence

| 字段 | 含义 |
|---|---|
| `occurrence_id` | source、kind、canonical name、span 和 owner 的确定性 ID |
| `kind` | `component/api/decorator/attribute/symbol/syntax/import_binding/import_use/field_read/field_write/call/string_literal/resource_reference` |
| `name` / `canonical_name` | 源码名称与可选的归一化名称 |
| `span` / `exact_range` | 1-based、end-inclusive 文件绝对行，以及 0-based、end-exclusive UTF-16 code-unit offset |
| `owner_ref` | tagged reference，精确指向 `declaration_id` 或 `region_id`；unresolved fact 为 `null` |
| `quality` / `provenance` | `exact/recovered/degraded/unresolved` 与 `L0/L1/recovered`；产生器版本保存在 `FileAnalysis.parser_version` |

### 7.3 ReviewRegion

| 字段 | 含义 |
|---|---|
| `region_id` | source、kind、symbol 和 span 的确定性 ID |
| `kind` | 第一版只有 `field_region \| import_region` |
| `symbol` | 字段或 import binding 的稳定展示名 |
| `span` / `exact_range` | 完整语法区域的文件绝对行与 UTF-16 exact range |
| `owner_declaration_id` | field 所属 host；文件级 import 为 `null` |
| `quality` / `provenance` | exact/recovered 结构质量和 `L1/recovered` 来源 |

owner 未解析的事实保留为 `quality=unresolved`、`owner_ref=null` 并产生
`unresolved_fact_owner`，不得伪造 exact owner 或进入 Unit evidence。Parser v2 sidecar 只有在
显式 `--output-schema file-analysis-v1` 时返回 v2 结构；不传参数仍返回冻结的
`code-facts-v1`。独立、人工审阅的 FileAnalysis Golden 不改写 Parser v1 expected/baseline。
当前 15-case Golden 完整比较全部 7 种 declaration kind、13 种 fact kind、两种 region、
quality/provenance、diagnostics 和输出顺序；同一行同名 occurrence、非 BMP 字符、scope-aware
import/API shadow、ERROR/missing recovery 和 unresolved owner 都有冻结真值。loader 对重复
JSON key/case、未知或缺失字段、source hash、UTF-16 boundary、owner/parent containment 以及
baseline provenance 漂移 fail-closed。

## 8. Declaration

当前字段继续保留：

```python
class Declaration:
    kind: struct | class | function | method | build_method | builder | ui_block
    name: str
    qualified_name: str
    span: SourceSpan
    parent_name: str | None
    text: str
```

RU-3 还会在可以与 v2 occurrence 对齐时填充兼容字段 `declaration_id`、`parent_id`、
`start_offset_utf16` 和 `end_offset_utf16`。正式 occurrence source of truth 仍是
`FileAnalysis.declarations`，不能靠 `qualified_name/parent_name` 猜测 identity 或 owner。

后续调整：

- `declaration_id` 使用路径、kind、qualified name 和 span 生成。
- 只保存 span，代码文本按需从文件切片，避免嵌套声明重复存储源码。
- `parent_id` 替代不唯一的 `parent_name`。
- 增加 parse confidence 和 diagnostics。

## 9. 当前 ReviewUnit

```python
class ReviewUnit:
    file: str
    unit_symbol: str
    unit_ref: str
    full_text: str
    changed_lines: list[int]
    file_changed_lines: list[int]
    unit_changed_lines: list[int]
    host_summary: HostSummary
    context_degraded: bool
    unit_id: str
    unit_kind: str
    source_span: ReviewUnitSpan
    context_span: ReviewUnitSpan
    changed_new_lines: list[int]
    selection_reason: str
    diagnostics: list[ReviewUnitDiagnostic]
    source_ref_id: str | None
    source_role: "base" | "head" | None
    change_atom_ids: list[str]
    changed_old_lines: list[int]
    owner_ref: OwnerRef | None
```

当前 `changed_lines` 与 `file_changed_lines` 仍作为兼容字段保留，`unit_ref` 也仍可能在同名
UI occurrence 间重复；新的 `unit_id` 才是去重 source of truth。`full_text` 已按
`context_span` 从文件源码切片，`changed_new_lines/changed_old_lines` 分别使用 head/base 的
1-based 文件绝对行。RU-3 兼容主链为非 fallback Unit 填充 `source_ref_id` 与 tagged
`owner_ref`；RU-4 的 ChangeSet 路径进一步填充 `source_role` 和 `change_atom_ids`，fallback
仍不伪造 owner。

### 9.1 ReviewUnit v1 过渡契约

RU-1 已增加以下字段，同时保留旧字段供现有调用方迁移：

```text
unit_id
unit_kind
source_span
context_span
changed_new_lines
selection_reason
diagnostics
```

`unit_id` 的输入至少包含：

```text
normalized path + declaration kind + qualified_name + start_line + end_line
```

identity 组件使用无歧义 percent-encoding；`@`、`:`、`%` 不能通过 path/symbol 边界注入出
相同 ID。若同一 path/kind/qualified name 甚至行 span 仍有多个 occurrence，RU-3 使用
`:O{start_offset_utf16}-{end_offset_utf16}` 后缀消除同一行重复 UI 节点碰撞；普通可由行 span
区分的 ID 保持原格式。兼容字段仍在输出中，但旧的缺字段 `ReviewUnit(...)` 构造方式不属于
兼容保证。

去重现在使用 `unit_id`，不再使用旧 `unit_ref`。同一 occurrence 的多个 hunk 合并；
同名但 span 不同的 occurrence 保持两个 Unit。RU-4 的 source-scoped identity 在原 ID 后追加：

```text
:R{role}:S{source_ref_sha256_digest}
```

例如 `src/A.ets@method:A.run:L4-L8:Rbase:S...` 与同路径 head Unit 不会碰撞；旧 FileHunk 路径
不追加该后缀，保持 v1 identity 兼容。真正 code-context budget 由 RU-5 的
`ContextPlanResult` 执行，不复用旧兼容字段中的 `token_budget`。

## 10. 当前 ReviewUnit v2

```jsonc
{
  "unit_id": "PhotoWall.ets@method:PhotoWall.loadImages:L14-L20:Rhead:S...",
  "file": "PhotoWall.ets",
  "source_ref_id": "code-source:sha256:...",
  "source_role": "head",
  "unit_kind": "method",
  "unit_symbol": "PhotoWall.loadImages",
  "source_span": {"start_line": 14, "end_line": 20},
  "context_span": {"start_line": 14, "end_line": 20},
  "change_atom_ids": ["change-atom:sha256:..."],
  "changed_old_lines": [],
  "changed_new_lines": [17, 18],
  "full_text": "async loadImages() { ... }",
  "host_summary": {},
  "selection_reason": "innermost_changed_declaration",
  "context_degraded": false,
  "diagnostics": []
}
```

`source_ref_id` 已在 RU-3 引入；RU-4 已增加 `source_role/change_atom_ids/changed_old_lines`。
兼容对象仍保留 `file/full_text/FileHunk`。`full_text` 必须严格等于 `context_span` 对应源码
切片；numbered excerpt 只能由它确定性派生。一个 change region 可以生成多个 ReviewUnit；一个
ReviewUnit 也可以合并多个 change region。关联代码不内嵌为自由 `related_context`，而由 RU-5
使用 SupportingSegment/RelationEdge 单独表达。

### 10.1 ReviewUnitBuildResult

RU-2 引入文件级结果信封，使“没有 Unit”和“调用者丢了结果”可区分：

| 字段 | 含义 |
|---|---|
| `schema_version` | ReviewUnit build 合同版本 |
| `mode` | `full \| diff`，与本批次选择语义一致 |
| `file_results` | 按 path 稳定排序的文件结果 |
| `diagnostics` | 批次级输入或合同诊断 |
| `change_set_id` | build-v3 对应的确定性 ChangeSet identity |
| `unassigned_change_atom_ids` | 未映射到任何 Unit 的 atom，升序且唯一 |

每个 file result 至少包含 `path`、可空 `source_ref_id`、稳定排序的 `units`、
`unassigned_hunk_lines`、`unassigned_change_atom_ids`、`parser_quality` 和 `diagnostics`。
RU-2 只填前者作为粗 FileHunk proxy；RU-3 已增加 head `source_ref_id`，并将 schema 升为
`review-unit-build-v2`；RU-4 的 `review-unit-build-v3` 增加顶层 `change_set_id`、聚合
`unassigned_change_atom_ids`，并要求 file result 携带 `changed_file_id/source_role`。diff 文件无 hunk、hunk 越界或
粗粒度 hunk 有未归属行时，必须通过这个信封显式表达，不能静默改为 full
review。

`review-unit-build-v1` 继续作为 RU-2 兼容 schema；旧 FileInput 主链输出
`review-unit-build-v2`，ChangeSet 路径输出 `review-unit-build-v3`。其中 `parser_quality` 使用 `parser_layer` 和排序去重后的 `warnings`，
只描述构建 Unit 所依据的完整文件 Parser 结果。兼容字段 `AnalysisResult.review_units` 必须
严格等于 `file_results[].units` 的稳定扁平视图。

当前顶层 `AnalysisResult` 固定为 `analysis-result-v1`，并强制携带
`FeatureRoutingResult`；即使 binary/empty 输入得到零 Unit，也必须携带版本化空路由结果。
调用方不能通过同时清空 build、parse、scope 和 feature 字段把正式结果降级成无重放保护的
legacy 形态。

### 10.2 RU-5 上下文规划最小对象

RU-5 不是只取 Top-1 Unit。所有直接改动 owner 都是 Primary `ReviewUnit`；预算只能筛选
Supporting，不能删除 Primary 或 changed lines。字段级最小合同如下：

| 对象 | 最小字段 | 核心不变量 |
|---|---|---|
| `ContextCandidate` | `candidate_id`、`primary_unit_id`、`review_question_id`、`relation_edge_id`、`relation_type`、`target_source_ref_id`、`target_span`、`estimated_tokens`、`necessity`、`provenance_ref` | `provenance_ref` 必须是目标 declaration/region owner；生产入口将 source/span/quality 与 FileAnalysis occurrence 精确交叉验证 |
| `SupportingSegment` | `segment_id`、`candidate_id`、`source_ref_id`、`source_span`、`source_text`、`question_binding`、`selection_reason`、`estimated_tokens`、`diagnostics` | `source_text` 严格等于 source/span 切片，且可追溯回一个 Primary、问题、owner 和 relation |
| `RelationEdge` | `edge_id`、`source_ref`、`target_ref`、`relation_type`、`strength`、`quality`、`evidence_refs`、`provenance_ref` | 类型、强度和 exact/degraded 质量是枚举；support edge 的 evidence 必须包含目标 owner |
| `ChangeGroup` | `group_id`、`primary_unit_ids`、`strong_edge_ids`、`diagnostics` | 仅 strong + exact 的 Primary-to-Primary edge 可建组；same-file/same-host 不单独构成强关系 |
| `ReviewContextBundle` | `bundle_id`、`group_id`、`primary_unit_ids`、`primary_question_bindings`、`supporting_segment_ids`、`relation_edge_ids`、`budget`、`dispatch_allowed`、`diagnostics` | 每个 bundle 只绑定一个 review question，但必须保留 group 内全部 Primary；超限或 required 缺失时禁止调度 |
| `ContextPlanResult` | `context_plan_id`、`planner_version`、`token_estimator_version`、`change_set_id`、`blocking_change_ids`、`primary_question_bindings`、`candidates`、`supporting_segments`、`relation_edges`、`change_groups`、`bundles`、`omitted_candidate_ids`、`omitted_candidates`、`budget_summary`、`diagnostics` | 这是 RU-5 唯一顶层产物；所有列表稳定排序，选中和舍弃都可追溯；未归属 ChangeAtom 与 binary ChangedFile 必须进入 blocker，禁止把不完整计划标成可调度 |

`necessity` 冻结为 `required/helpful/distractor`；relation type 冻结为
`lifecycle_pair/state_access/direct_call/direct_caller/change_correspondence/same_host/same_file`；
前四类 exact 关系可以产生 Supporting，前五类 strong + exact Primary-to-Primary 关系可以建组。
`change_correspondence` 不靠名称猜测：Planner 根据 base/head Primary 共享的 ChangeAtom 自动
生成，用于保证 replacement 的改前/改后代码进入同一个 ChangeGroup。该类型只能由 Planner
内部派生；调用方不能注入同名 edge，ContextCandidate 也不能把它用作 Supporting 关系。
遗漏原因至少包含 `distractor_rejected/budget_exceeded/context_blocked/relation_degraded`；
`primary_exceeds_budget/context_insufficient/relation_degraded` 是结构化 diagnostic，不是自由文本。

`CodeAnalyzer.plan_context(...)` 是生产入口：它必须消费完整、已验证的
`review-unit-build-v3 AnalysisResult`，并全量转发其中的 Primary。额外 Supporting 文件只能以
固定 `CodeSourceSnapshot + FileAnalysis` 显式注入；任意表达式、字符串中段或 recovered
boundary 不能伪装成 exact Supporting。RU-5 不负责递归扫描或构建全仓索引。

`code_context_budget` 是 per-bundle 源码预算，使用版本化的 `arkts-code-token-v1`。扫描器把
ArkTS syntax 和 trivia 都切成确定性 chunk，每个 chunk 计
`max(1, ceil(UTF-8 bytes / 4))`，因此长字符串、注释、Unicode 和空白不会被当成一个廉价
token。Planner 按 review question 拆 bundle：required 在该问题的每个可调度 bundle 中重复，
helpful 使用稳定 first-fit 分箱。所有 bundle 保留全部 Primary；任何可调度 bundle 的
`total_tokens` 都不得超过 `limit`。

## 11. Unit Feature Context 与 Feature Routing v1 / candidate v2-v3

RU-3 从 `FileAnalysis.fact_occurrences` 按 owner/span 投影 `UnitFactScope`；Feature Routing 在
该边界上生产正式 `feature-routing-v1`。默认生产链仍为：

```text
FileAnalysis + ReviewUnit
-> UnitFactScope(unit_exact, file_hints)
-> FeatureRouter(tags-v1, dimensions-v1)
-> UnitFeatureProfile[]
-> FeatureRoutingResult
```

只有显式注入 `tag-config-v3` 的 FR-02 development-regression 评估才输出
`feature-routing-v2`。该非默认 output 当前不进入默认 `CodeAnalyzer`、
ContextPlanner 或生产激活链；`tag-config-v1/v2` 及 `feature-routing-v1` 的既有
合同保持冻结。

FR-02B 显式注入 `tag-config-v4`，输出 `feature-routing-v3`。v3 为每个归一化
symbol 增加 owner-role evidence，仍只是 shadow candidate contract。它复用既有
FileAnalysis declaration、decorator、owner 和 occurrence ID，不修改 Parser schema 或 Parser v1
行为，也不改写默认 v1 result。

`unit_exact` 只收录 owner 为 Unit 本身或其后代、完整落在 Unit source span 内、质量为
exact/recovered 的 occurrence，`exact_occurrence_ids` 保留来源。fallback、owner 未解析或
degraded/unresolved fact 不会被提升为 exact。`file_hints` 是同一 `source_ref_id` 的文件级存在
信号，不能声称属于 Unit，也不能成为 Finding evidence。

### 11.1 UnitFeatureProfile

每个 Unit 的正式 Feature 产物包含以下字段。下例为字段结构节选，省略的 Active
`dimension_routes` 和数组元素不能在真实序列化中省略：

```jsonc
{
  "profile_id": "feature-profile:sha256:...",
  "unit_id": "...",
  "source_ref_id": "code-source:sha256:...",
  "feature_config_version": "feature-config:sha256:...",
  "exact_tags": ["has_timer"],
  "routing_tags": ["has_timer"],
  "shadow_exact_tags": [],
  "shadow_routing_tags": [],
  "tag_matches": [
    {
      "tag_id": "has_timer",
      "status": "Active",
      "scope": "file_hint",
      "signals": [{"kind": "apis", "value": "setInterval"}]
    },
    {
      "tag_id": "has_timer",
      "status": "Active",
      "scope": "unit_exact",
      "signals": [{"kind": "apis", "value": "setInterval"}]
    }
  ],
  "dimensions": ["DIM-01", "DIM-02", "DIM-03", "DIM-04", "DIM-05", "DIM-06", "DIM-12"],
  "always_check_dimensions": ["DIM-01", "DIM-02", "DIM-03", "DIM-04", "DIM-05", "DIM-12"],
  "retrieval_dimensions": ["DIM-06"],
  "routing_dimensions": ["DIM-06"],
  "shadow_dimensions": [],
  "dimension_routes": [
    {
      "dimension_id": "DIM-06",
      "always_check": false,
      "retrieval_policy": "signal_required",
      "review_enabled": true,
      "retrieval_enabled": true,
      "routing_enabled": true,
      "signal_scope": "unit_exact",
      "matched_exact_tags": ["has_timer"],
      "matched_routing_tags": []
    }
  ],
  "review_question_ids": ["RQ-correctness", "RQ-resource"],
  "shadow_review_question_ids": [],
  "diagnostics": []
}
```

`feature-routing-v1` 的每个 Feature signal 严格只有 `kind/value`。
`feature-routing-v2` 保留这两个字段，并允许归一化 signal 原子增加
`operator/normalized_value`；两者必须同时存在。当前唯一允许的 operator 是
`any_symbol_leaf`：

```json
{
  "kind": "symbols",
  "value": "Index.aboutToAppear",
  "operator": "any_symbol_leaf",
  "normalized_value": "aboutToAppear"
}
```

原始 `value` 保留 provenance，`normalized_value` 只是用于规则匹配的最后一个点分段；
它不证明 symbol owner 的 ArkUI 类型。

`feature-routing-v3` 的 owner-aware signal 使用
`operator=any_unit_symbol_leaf_with_owner_role`，并且必须同时保存：

```jsonc
{
  "kind": "symbols",
  "value": "Index.aboutToAppear",
  "normalized_value": "aboutToAppear",
  "operator": "any_unit_symbol_leaf_with_owner_role",
  "owner_role": "arkui_custom_component",
  "symbol_occurrence_id": "occurrence:sha256:...",
  "direct_owner_declaration_id": "declaration:sha256:...",
  "enclosing_owner_declaration_id": "declaration:sha256:...",
  "role_evidence_occurrence_ids": ["occurrence:sha256:..."]
}
```

`symbol_occurrence_id` 必须指向当前 raw symbol；`direct_owner_declaration_id` 保留 symbol
直接 owner，`enclosing_owner_declaration_id` 指向承载 ArkUI role 的 enclosing declaration；
`role_evidence_occurrence_ids` 必须是支撑该 role 的结构化 decorator/owner evidence。不允许
借用同文件其他 declaration 的 `@Component` 或 `@Entry` 为当前 symbol 背书。
Method Unit 只绑定其自身 method declaration；struct Unit 可以绑定该 struct 的直接 lifecycle
method 子声明。嵌套 ordinary class 的同名 method 不是直接子声明，必须 abstain，不能从外层
ArkUI struct 继承 owner role。

FR-02B 只允许两类 role：`aboutToAppear/aboutToDisappear` 需要
`arkui_custom_component`；`onBackPress/onPageHide/onPageShow` 需要
`arkui_router_page`。`onReady` 不在 v4 owner-aware exact 映射中，但仍可由
`any_file_symbol_leaf` 产生 routing-only hint。

V4 的 file-hint trace 使用独立的 `operator=any_file_symbol_leaf`，只保留 raw
symbol 和 normalized leaf，不声称 owner role。该 operator 只在 `file_hint` scope 求值；
`unit_exact` 必须忽略它。反之，`any_unit_symbol_leaf_with_owner_role` 只在
`unit_exact` 求值，不得从 file-level facts 制造 exact Tag。

字段语义：

| 字段 | 冻结不变量 |
|---|---|
| `exact_tags` | 只等于 Active、`unit_exact` scope 的 TagMatch |
| `routing_tags` | 只等于 Active、`file_hint` scope 的 TagMatch |
| `shadow_*` | 只承载 Draft 结果，不进入正式执行 |
| `dimensions` | `always_check` 或 exact Tag 命中的 Active Dimension |
| `retrieval_dimensions` | policy 允许且有 exact signal 的正式检索维度 |
| `routing_dimensions` | policy 允许且有 exact 或 hint signal 的保守候选维度 |
| `review_question_ids` | `always_bind` 或 exact Tag 命中的 Active Question |
| `diagnostics` | v1/v2 来自 `UnitFactScope.unit_owner_unresolved`；v3 还保留 owner-context abstain/quality diagnostics |

每个 `DimensionRoute` 完整记录 `always_check`、`retrieval_policy`、三个 enabled flag、
`signal_scope` 和命中的 exact/routing Tags。hint-only signal 可以令 `routing_enabled=true`，但
不能令 `retrieval_enabled=true`、不能进入 Unit exact Dimensions，也不绑定专项 Question。

当前 `UnitFactScope` 没有携带完整 parser quality；Feature profile 是否有 Tag 不能证明 Parser
没有 ERROR/missing node。Parser 质量继续从 `FileAnalysis.parser_quality`、Analysis metadata 和
ReviewUnit diagnostics 读取。

### 11.2 FeatureRoutingResult

以下同样只展示顶层字段，不是可直接反序列化的完整 fixture：

```jsonc
{
  "schema_version": "feature-routing-v1",
  "feature_routing_id": "feature-routing:sha256:...",
  "feature_config_version": "feature-config:sha256:...",
  "tags_config_version": "tags-v1",
  "dimensions_config_version": "dimensions-v1",
  "units": [],
  "mr_dimensions": [],
  "question_bindings": [
    {"primary_unit_id": "...", "review_question_id": "RQ-correctness"}
  ],
  "diagnostics": []
}
```

FR-02 candidate 使用独立的顶层 schema：

```jsonc
{
  "schema_version": "feature-routing-v2",
  "feature_routing_id": "feature-routing:sha256:...",
  "feature_config_version": "feature-config:sha256:...",
  "tags_config_version": "tags-lifecycle-symbol-leaf-shadow-v1",
  "dimensions_config_version": "dimensions-v1",
  "units": [],
  "mr_dimensions": [],
  "question_bindings": [],
  "diagnostics": []
}
```

`feature-routing-v2` 是 normalized signal provenance 的 schema 演进，不表示 candidate Tag
已获得生产资格。v1 artifact 不得增加 v2-only signal 字段；v2 replay 必须
使用同一 `tag-config-v3` candidate 及 feature-config fingerprint。

FR-02B owner-aware candidate 则使用：

```jsonc
{
  "schema_version": "feature-routing-v3",
  "feature_routing_id": "feature-routing:sha256:...",
  "feature_config_version": "feature-config:sha256:...",
  "tags_config_version": "tags-lifecycle-owner-role-shadow-v1",
  "dimensions_config_version": "dimensions-v1",
  "units": [],
  "mr_dimensions": [],
  "question_bindings": [],
  "diagnostics": []
}
```

v3 replay 除同一 v4 config/fingerprint 外，还必须使用同一 ReviewUnit 和 FileAnalysis
owner/evidence 输入。显式入口把 `UnitFactScope + ReviewUnit + FileAnalysis` 绑定为
`OwnerAwareRoutingInput`，再调用 `FeatureRouter.route_owner_aware_shadow(...)`；默认
`FeatureRouter.route(scopes)` 与 `CodeAnalyzer` 链不接受这类 candidate-only 输入。
对 v4 config 调用旧 `route(scopes)` 必须报 contract error，不能在缺少 ReviewUnit/FileAnalysis
owner input 时静默退化为 routing-only 结果。
`feature-routing-v3` artifact 必须调用 `validate_owner_aware_replay(inputs, config)`；通用
`validate_replay(scopes, config)` 会 fail closed，避免遗漏 owner evidence。
`file_hints` 只能通过 `any_file_symbol_leaf` 生成 routing Tag；
不得由 file-level decorator 推导当前 Unit 的 owner-aware exact signal。上述新 schema 均不代表
生产激活。

顶层 `units` 按 unit ID 排序且必须精确覆盖全部 UnitFactScopes；`mr_dimensions` 等于所有 Unit
`dimensions + routing_dimensions` 的稳定并集。Question bindings 必须严格等于各 profile 的
Active `review_question_ids`。所有 profile/result identity 都包含配置 fingerprint 和完整语义；
v1/v2 通过 `validate_replay(scopes, config)`、v3 通过
`validate_owner_aware_replay(inputs, config)` 重新路由并要求对象完全相等。模型构造器自身只证明图内部
一致；来自存储或网络的结果必须结合原始 scopes 和对应配置重放后，才能作为可信路由产物。

Feature Routing 拥有 Question registry 和适用性选择；ReviewUnit/Context Planner 继续拥有
`QuestionBinding` 的承载形状、ChangeGroup、bundle 和预算。`CodeAnalyzer.plan_context(...)`
把上面的二字段 binding 转换为现有 Context Planner `QuestionBinding`；兼容入参若出现，只能
验证相等，不能覆盖正式结果。

### 11.3 Lifecycle blind holdout artifacts

FR-02B 的独立质量证据不写入 `FeatureRoutingResult`，也不升级现有
`tag-retrieval-truth-v2`。它使用四类独立、closed-schema、自哈希 artifact：

| Schema | 内容 | 明确禁止 |
|---|---|---|
| `lifecycle-holdout-selection-v1` | 无标签 case、source family/path/hash、candidate runtime/environment、evaluation harness、development exclusions、固定 strata/quality gates、selector attestation | expected/actual label、candidate output |
| `lifecycle-holdout-review-packet-v1` | canonical Tag contract/review policy、selection 对应 source span、opaque case ID | candidate identity/output、selection stratum/rank、expected/actual 字段 |
| `lifecycle-holdout-review-receipt-v1` | 一名 human reviewer 的完整 case decision、ReviewUnit identity/evidence、blind attestation | 缺 case、第二 reviewer 内容、自动生成标签 |
| `lifecycle-holdout-consensus-v1` | 恰好两份不同 receipt 的逐 case votes、agreed/unresolved、release blockers | 丢弃 disagreement、揭盲后第三票 |

Selection 绑定候选 commit `9b7a828449cbe760ce9374d222f75c48b6f5c852`、
`feature-config:sha256:844418e3d7938c816fd3b64b62cdae3d1753d286d50a6a103406838ed6db01e7`
和 `tag-config-v4/feature-routing-v3`。Runtime snapshot 覆盖 candidate commit 下完整
`src/arkts_code_reviewer` Python tree、默认 tags/dimensions、Parser
sidecar、candidate config；`runtime_environment` 另外固定 Python version/packages/platform、
Node version/executable hash 和 `node_modules` tree fingerprint。`evaluation_harness_commit`、
`evaluation_harness_files`、`evaluation_harness_fingerprint` 单独冻结 contract、evaluator、
manifest 和 CLI 工具。

Candidate freeze 同时声明其设计期 corpus exposure 是
`applications_app_samples@8255a2987f70317cc3a2a4d46044c6b55f092bb3` 的
`entire_tracked_repository`。因此 selection repository 必须是该 revision 的严格后继 descendant，
且每个 selected source 相对 exposure tree 都必须同时使用全新 path-derived family、全新 path、
全新 Git blob/content；同 revision 永远不满足独立性。现有 canonical development Truth 还提供
不可替换的 `source_family_ids/source_paths/content_sha256` 排除集。

Selection policy 的 `dataset_kind` 固定为 `purposive_stratified_challenge_holdout`，不是随机样本，
没有 inclusion-probability 或 natural-prevalence 声明。V1 固定 32 case/32 family：
`component_v1_positive=4`、`component_v2_positive=4`、`router_page_positive=8`，以及
`nested_owner_negative/non_entry_page_negative/ordinary_owner_negative/routing_only_negative`
各 4。当前 eligible corpus 没有独立 non-DocsSample `@ComponentV2` family，因此尚不能构造真实
selection，不能弱化或替换该 stratum。

Packet builder 从仓库固定路径加载 `tag_contract.md/review_policy.md`，没有调用方可替换的 CLI
参数。Post-seal evaluator 会用 sealed selection、verified checkout 和 canonical 文本重建 packet，
再由两份 receipt 重建 consensus；任一对象不相等都 fail closed。Artifact 中的独立/盲审字段、
negative stratum 和“first run”顺序只是 human-process attestation，不是身份或密码学证明。

正式 CLI 必须在全新 seal checkout 中，用仓库外、非 editable 的隔离 virtualenv 以 `-P -B -S`
和空 `PYTHONPATH` 启动。纯标准库 preflight 会验证 Git seal、五份 artifact 的 committed bytes、
完整 `src` import closure、candidate runtime/environment 和 evaluation harness，并拒绝 ignored
bytecode、native extension、symlink 或额外源码，成功前不把仓库路径加入 `sys.path`。随后才 import typed evaluator；
由于包存在 eager import，此时会执行已经验证过的项目源码，但仍不会加载 candidate config、实例化
或运行 `FeatureRouter`。Typed validation 再检查 complete consensus、source checkout、policy、
development exclusions、exposure boundary，并重建 packet/consensus；全部相等后才运行候选。正式
进程还要求 `HEAD == seal_revision`、clean worktree、source bytes 与 pinned revision Git blob
完全相等。Parser、ReviewUnit、UnitFactScope、owner provenance、
challenge-owner、file-hint promotion 和 routing-only risk gate 均固定为 0。

报告可给出 `evidence_ready`；`--omit-cases` 时仍必须输出 `case_details_omitted=true` marker，最终
输出形状由 `evaluation_id` 绑定：它是去掉自身字段后 canonical JSON 的
`lifecycle-owner-role-holdout-evaluation:sha256:*` hash，但不认证 runner 身份。
无论证据是否通过，`production_activation.activation_ready` 固定为 `false`，任何 artifact 或门禁
都不会自动修改默认 `tags-v1`。当前没有真实 selection、receipt、consensus 或 result，candidate
尚未通过该链运行；生产级执行还需要独立 CI/container 与外部身份/权限控制。冻结的 Python
直接/传递依赖版本不等于依赖 bytes 的密码学证明，后者仍属于可信外部环境边界。

### 11.4 Near-duplicate Pair Truth 与校准 artifacts

通用 Tag Truth 的 near-duplicate 校准不复用 lifecycle 专项 schema，也不修改
`FeatureRoutingResult`。它由以下 closed-schema、自哈希对象组成：

| Schema / contract version | 对象 | 当前职责 |
|---|---|---|
| `tag-truth-v2-nd-pair-selection-v1` | `NearDuplicatePairSelectionV1` | 固定 Pair 成员、calibration/acceptance-holdout split、coverage stratum 与 leakage component |
| `tag-truth-v2-nd-pair-packet-v1` | `NearDuplicatePairReviewPacketV1` | 隐去显式 path、split、component、rank 和候选输出的双边完整正文 |
| `tag-truth-v2-nd-pair-receipt-v1` | `NearDuplicatePairReviewReceiptV1` | 一名独立 human reviewer 的完整 Pair label、双边 evidence line、rationale 与 blinding attestation |
| `tag-truth-v2-nd-pair-consensus-v1` | `NearDuplicatePairConsensusV1` | 无损保留两票；一致时发布 duplicate/independent/ambiguous，不一致时 unresolved |
| `tag-truth-v2-nd-pair-oracle-v1` | `NearDuplicatePairOraclePredictionSetV1` | 对 manifest Pair 穷举运行冻结的 Stage-2D1 v1 similarity 语义 |
| `tag-truth-v2-nd-calibration-gate-v1` | `NearDuplicateCalibrationGateV1` | 冻结样本数、component 数、P/R、Wilson、Kappa、abstain 与 fatal-error 门禁 |
| `tag-truth-v2-nd-policy-freeze-v1` | `NearDuplicatePolicyCandidateFreezeV1` | 在释放 holdout 前绑定 policy、Oracle semantics、gate、candidate commit 和声明的 verifier closure |
| `tag-truth-v2-nd-holdout-release-v1` | `NearDuplicateHoldoutReleaseReceiptV1` | 绑定 selection/freeze/custodian，并要求 `released_at > frozen_at` |
| `tag-truth-v2-nd-calibration-report-v1` | `NearDuplicateCalibrationReportV1` | 保存逐 Pair 结果、两 split 指标、review quality、blocker 和 freeze/release identity |
| `tag-truth-v2-nd-policy-approval-receipt-v1` | `NearDuplicatePolicyApprovalReceiptV1` | 记录独立 approver 对未来 verified screening policy semantics 的决定 |

数据链固定为：

```text
PairSelection
-> PairReviewPacket
-> ReviewReceipt A + ReviewReceipt B
-> PairConsensus
-> exhaustive Oracle predictions
-> frozen CalibrationGate
-> PolicyCandidateFreeze
-> HoldoutReleaseReceipt
-> CalibrationReport
-> typed-artifact-chain-verified PolicyApprovalReceipt
```

关键不变量：

- `normalized_body_sha256` 与 `template_cluster_id` 必须由 member text 重新计算；source family
  跨 revision 联通，manual related group 只能补充 leakage key。
- 一个 leakage component 不得同时出现在 calibration 和 acceptance holdout。
- acceptance holdout 至少需要 80 个 duplicate component 和 80 个 independent component；
  每个二元指标 Pair 必须独占一个 component，不能用相关 Pair 虚增 Wilson 分母。
- `file_file` 与 `unit_unit` 双向比较，`unit_file` 只比较 left Unit 到 right file；Pair reducer
  固定为 `duplicate > gray > abstain > clear`。short exact 仍是 duplicate，只有 canonical
  clear 的短 probe 才转为 abstain。
- `oracle_semantics_fingerprint` 只标识上述声明语义，不是 Python/import/runtime code identity。
  Freeze 中的 Git blob closure 当前也只是调用方声明，尚未由 preflight 对 candidate commit
  和 current checkout 做逐字节验证。
- Artifact 的 self-hash 只证明内容 identity。正式 report 必须从 Selection、Packet、两份
  Receipt、Consensus、Policy、Oracle predictions、Gate、Freeze 和 HoldoutRelease 完整重建；
  正式 approval builder/verifier 必须先执行同一 full rebuild。重建根是调用方提供并通过
  self-hash 校验的 Selection，不证明 Pair source Git provenance。
- `calibration_gate_status=passed` 只表示 `eligible_for_human_review`。Report 固定
  `policy_approval_status=not_approved`；即使 PolicyApprovalReceipt 的 decision 是
  `approved`，Report 和 ApprovalReceipt 仍固定
  `evidence_qualification_status=not_qualified`。Custodian 不得兼任 reviewer；
  approver 不得是 reviewer/custodian；审批证明和记录时间不得早于 holdout release，当前秒级
  timestamp 合同允许相等；failed/not-eligible report 不能支持 approved。Approved scope 只覆盖
  未来 verified screening policy semantics。

当前仓库只有合同和合成/负向测试，没有真实 PairSelection、人工 Receipt、Consensus、
Freeze/Release、CalibrationReport 或 ApprovalReceipt。该链不实现 policy v2、screening v2，
也不修改默认 Tag/Dimension/RQ、Feature config fingerprint、Golden 或 candidate runtime。

### 11.5 当前 AI Tag shadow evaluation artifacts

`ai-tag-shadow-unit-evaluation-v1` 与 `ai-tag-shadow-evaluation-report-v1` 是已经实现的
evaluation/audit 产物，不是 Feature Routing 或正式 Hybrid 运行产物。输入闭包从调用方提供的 sealed
`ReviewUnitAnalysisCard` 开始，使用 Builder 绑定的 Catalog/Prompt/model policy 确定性重建 full-24
Request 与 `VerifiedAITagDispatchEnvelope`；默认 wrapper 使用当前仓库默认资产。随后核对 non-formal
`AITagResponseValidation`：

```text
caller-supplied AnalysisCard
-> Builder-bound full-24 Request/Envelope deterministic rebuild
-> sealed ResponseValidation against-envelope verification
-> AITagShadowUnitEvaluation
-> canonical multi-Unit AITagShadowEvaluationReport
```

每个 Unit 记录 static exact、static routing 和 `validated_content_decision` 三个独立轴；
`unit_comparison_status` 只由 static exact × validated-content decision 的既有 reducer 产生。Routing
仍是 file hint，不参与 `agreement_positive/disagreement`。只有 `valid_shape` 才能携带完整 24 项
decision；`invalid_output/unavailable_claim` 的 decision 全部为空，不能被解释成
`not_supported` 或 `abstain`。沿用 reducer 的 `*_due_execution` 名称只表示没有可用的 validated
content decision，不证明 provider attempt 实际发生；产物因此使用 `reported_attempt_count`，其数值仍是
ResponseValidation 携带的 caller claim。

Batch report 按稳定 Unit identity 排序，拒绝重复 Unit/Card/View/Request/Envelope/Validation，并要求
feature config、context/projection policy、taxonomy、Catalog、Prompt 和 model policy 一致。它从嵌入的
Unit 明细重建 response/source counts、decision/comparison counts、24 个逐 Tag aggregate、reported
usage 和 reported latency；完整 verifier 还会从调用方 roots 重建每个 Unit 和整个 report，不能用
self-hash 代替输入闭包。V1 的 `collection_scope=caller_supplied_input_set_not_campaign_bound`，没有绑定
sealed selection、dataset、ChangeSet 或 analysis-run manifest，因此只是任意调用方输入集合的机械
汇总，不是可重放的正式 campaign artifact。

两个 artifact 都固定为 `evidence_qualification_status=not_qualified`、
`production_qualified=false`，并保留 provider attribution、独立 Tag Truth、production prevalence
和文档 Retrieval Truth 缺失的 blockers；另有
`analysis_card_upstream_provenance_not_rebuilt` 与 `evaluation_campaign_manifest_not_bound` 明确限制
verifier closure。它们没有 `exact_tags/routing_tags/ai_inferred_tags`、
Dimension、RQ、Result/Outcome、RetrievalRequest、Evidence 或 Finding 字段；builder 不访问 provider
或 credential，也不构造 `HybridFeatureAnalysisResult`。因此这些分布只能证明聚合合同和当前输入的
诊断状态，不能产生 TP/FP/FN/TN、Precision/Recall、模型稳定性或生产启用结论。Card 之前的 Parser、
ReviewUnit、Feature Routing 与 Git provenance 仍不在该 verifier closure 内。

### 11.6 当前 AI Tag shadow campaign 准备与 inspection artifacts

`ai-tag-shadow-campaign-manifest-v1` 是已经实现的 evaluation-only campaign **准备合同**。它从调用方
提供的 `AnalysisResult + ContextPlanResult + ChangeSet + SourceSnapshotBundle` 和显式选中的
ReviewUnit 集合开始，使用 provider-egress Card policy、当前 24-Tag Catalog/Prompt/model policy、
shadow provider policy 与 limits，逐 Unit 确定性重建：

```text
caller-supplied upstream graph + selected Unit IDs
-> AnalysisCardBuilder.build_many
-> AITagModelView
-> full-24 AITagAnalysisRequest
-> VerifiedAITagDispatchEnvelope
-> per-Unit AITagShadowDispatchPlan
-> AITagShadowCampaignManifest
```

Manifest 只冻结同一次 selection 中的上游 graph/source identities、共享 policy/assets fingerprints 和
每个 Unit 的 Card/View/Request/Envelope/Plan 引用。选中 Unit 使用 canonical 排序，因此调用方输入
顺序不改变 campaign identity；重复或未知 Unit、跨 Unit artifact 拼接、共享配置漂移和 limits 漂移
均 fail-closed。`verify_against_upstream` 会从调用方提供的完整上游对象重新执行整条 Builder 链，而
不把 manifest self-hash 当作上游 Git provenance 或 source authenticity 证明。

`ai-tag-shadow-campaign-inspection-v1` 是由 manifest/bundle 派生的 metadata-only 只读投影，不是第二份
dispatch 真值。它不输出可能携带源码路径、qualified symbol 和行号的明文 `unit_id`，只包含 opaque
source/Card/View/Request/Envelope/Plan identities、最终 wire body hash/byte
length、endpoint/model、timeout/output/response limits 和汇总数量；不包含源码、Prompt 正文、Tag
合同正文、wire JSON、credential、响应或 Tag judgment。repo-only
`tools/inspect_ai_tag_shadow_campaign.py` 只校验并规范化该 inspection JSON，不读取环境 credential，
没有 execute/live/approval/state 参数，并固定声明 `network_attempted=false`、
`credential_accessed=false`。这些字段是本地 inspection 合同，不是外部不可否认证明。

Campaign-aware evaluation adapter 要求调用方提供与 manifest Plan ID 集合完全相等的 mapping，并验证
每个 `AITagResponseValidation` 绑定对应 Unit 的 Envelope；缺失、额外或跨 Unit 调换均拒绝。Validation
本身不绑定 Plan ID、timeout/output/response limits 或 Plan execution receipt，因此该 mapping 只证明
caller-keyed Plan coverage + Envelope-bound validation，不能证明 response 来自对应 Plan 的实际执行。
adapter 随后复用 11.5 的 v1 evaluator，而不是创造第二套 report schema。生成的 report 因而仍明确记录
`collection_scope=caller_supplied_input_set_not_campaign_bound` 与既有 qualification blockers；manifest
只加强本次 adapter 的输入映射和 full rebuild closure，不会把 v1 report 升级为 sealed execution
campaign、provider attribution、人工 Truth 或 qualified evidence。该 adapter 仍不能消费零 attempt 的
`skipped_budget/not_run` Unit；这类 Unit 缺少 ResponseValidation 时必须 fail-closed，不能静默省略或
伪造为 `not_supported`。11.7 的 execution artifact 是独立的运行审计合同，不会反向放宽这个 evaluator
输入合同。

该链没有发送 DeepSeek 请求，没有读取 `.env`，没有访问 Retrieval/Knowledge，也不产生 formal
Result/Outcome、Hybrid、Evidence 或 Finding。它证明的是多 Unit selection、逐 Unit outbound plan、
不含路径/符号明文的 inspection 和 fake/non-formal validation 到既有 report 的确定性连接；仓库仍没有真实代码
multi-Unit campaign、人工 Unit-exact Truth、真实 P/R 或 N-run 稳定性证据。

### 11.7 当前 AI Tag shadow campaign execution artifacts

`ai-tag-shadow-campaign-unit-execution-v1` 与
`ai-tag-shadow-campaign-execution-result-v1` 是已经实现的多 ReviewUnit shadow **运行审计合同**。
它们消费 11.6 的完整 Campaign Bundle、调用方持有的上游重建 roots、与每个 Plan 一一对应的
Claims/Gate/transport runtime binding，以及显式 Campaign 总量与 wall-clock limits。Harness 先重建完整
Campaign，再按 Manifest 的 canonical Unit/Plan 顺序逐个执行；每个 Plan 最多一次 attempt，固定无重试，
也没有 batch body 或跨 Unit 合并判断。Harness 默认同时禁止真实固定 HTTP transport 与 injected test
transport；只有 `binding.transport is None` 才表示仓库内部固定 live transport，任何调用方传入的对象
（包括 `_HttpxDeepSeekShadowTransport` 的 exact instance）都属于 injected transport。调用方必须分别显式
opt-in 对应类别，否则保持零 dispatch。live transport 的 event-loop/httpx process preflight 发生在任何
Gate authorization、credential 读取和 reservation marker 消费之前：

```text
trusted upstream graph + Campaign Bundle
-> exact per-Plan runtime binding coverage
-> canonical sequential per-Plan authorization/dispatch
-> AITagShadowCampaignUnitExecution[]
-> AITagShadowCampaignExecutionResult
```

每个 Unit 的 `dispatch_disposition` 只能是：

- `attempted`：确实产生一次 Attempt 与 Observation；
- `skipped_budget`：本地 Gate 没有预留预算，`attempt_count=0`；
- `not_run`：本地外发未批准、credential 未配置，或剩余 Campaign wall-clock admission budget
  已不足以容纳下一个 Plan 的完整 timeout，
  `attempt_count=0`。

零 attempt Unit 必须携带内容寻址的
`ai-tag-shadow-campaign-non-attempt-receipt-v1` 本地观察收据，用它绑定 Plan、Claims、disposition、reason
和 control stage；不得携带 Attempt、Observation、Response、Validation 或 OuterDiagnostic 引用。该收据
防止只改 Result 自称另一种 denial，但仍只是进程内 observation，不是外部授权证明。
`attempted` Unit 进一步保留互斥的 outcome，而不是把失败改写成空的 24-Tag negative：

| outcome | 必须存在的运行产物 | 明确不存在的产物 |
|---|---|---|
| `valid_shape` | Attempt、Observation、ObservedResponse、ResponseValidation | OuterDiagnostic |
| `invalid_output_inner` | Attempt、Observation、ObservedResponse、ResponseValidation | OuterDiagnostic |
| `invalid_output_outer` | Attempt、Observation、OuterDiagnostic | ObservedResponse、ResponseValidation |
| `provider_client_error` / `provider_rate_limited` / `provider_server_error` | Attempt、Observation | ObservedResponse、ResponseValidation、OuterDiagnostic |
| `provider_timeout` / `provider_transport_error` / `provider_response_too_large` | Attempt、Observation | ObservedResponse、ResponseValidation、OuterDiagnostic |

Result 必须精确覆盖 Manifest 的全部 Plan，保存逐 Unit 内容寻址引用，并从 Unit 明细机械重建
planned/attempted/skipped/not-run、各 outcome、transport evidence、non-attempt receipt 和各类
attempt/response receipt/diagnostic counts。
`execution_policy_version` 固定为
`canonical_order_per_plan_single_attempt_no_retry_v1`。已知的 429、5xx、timeout、transport 和响应过大
只记录当前 Plan 的 observation，Harness 继续处理后续 Plan；Plan/Claims 不受信、runtime binding
coverage 不完整或其他未知授权错误仍 fail-closed，而不是伪装成 `not_run`。

Execution limit schema 另有不可放大的硬上限：64 Units、64,000,000 outbound body bytes、262,144
output tokens、128,000,000 response bytes 和 3,600,000 ms Campaign admission cap。Harness 在授权前和授权后
各检查一次剩余 admission budget；若授权后已不足，会撤销未使用 capability 并记录零网络 attempt 的
non-attempt receipt。该 cap 是 dispatch admission 边界；injected transport 自身是否阻塞或联网仍不受仓库
证明。

`verify_ai_tag_shadow_campaign_execution_result(...)` 必须另收调用方持有的 `expected_limits`；Result 自带的
limits 只是待验证 evidence，不能充当自己的信任根。verifier 有两个明确分层：

1. **Persistent graph verifier**：不需要原始响应正文。它从调用方 roots 重建 Campaign，要求 evidence
   mapping 精确覆盖全部 Plan，并核对 Plan/Claims、non-attempt receipt、Attempt、Observation、ObservedResponse、
   ResponseValidation/OuterDiagnostic 的引用、transport/status/reason 矩阵、逐 Unit seal、汇总 counts 和
   Result identity。
2. **Caller raw-bytes full rebuild**：调用方可额外提供与所有 `response_received` Plan 精确相等的
   `raw_response_body_by_plan_id`。verifier 随后复用单 Plan raw-byte verifier，重新解析并核对 body
   hash、outer/inner shape、receipt、validation 与 observation。Result 不保存 raw bytes；没有这些调用方
   bytes 时，persistent graph verifier 不能声称重建了供应方原始响应。

这两个 schema 固定 `evidence_qualification_status=not_qualified`、
`production_qualified=false`，并声明 `shadow_only_no_hybrid_no_retrieval`。本地 Gate/receipt 只证明当前
进程记录的控制与 observation，不是外部授权、provider signature、受信 runner signature 或 source Git
provenance；injected transport 也不能证明发生真实网络通信。产物不会生成 formal
`AITagAnalysisResult/AITagExecutionOutcome`，不会进入 `HybridFeatureAnalysisResult`、Retrieval、
Evidence 或 Finding。

`campaign_live_smoke.py` 与 `tools/run_deepseek_shadow_campaign_smoke.py` 只为一份 package-owned、hash-locked
的 4-ReviewUnit 合成 Campaign 提供受控入口。默认命令只输出 metadata inspection、返回 preflight 状态，
不构造 credential provider、不读取 `.env`、不 dispatch。真实执行必须逐字绑定 inspection 给出的
Campaign ID、Plan-set digest、全部 caps 和固定 acknowledgement，并为每个 Plan 在发送前用 `0700`
目录/`0600` 文件原子消费一次本地 reservation marker；state/marker 通过 directory fd 与 `O_NOFOLLOW`
（平台支持时）打开，marker 和目录都执行 `fsync`，同一 state directory 不自动 retry/resume/reset。
该固定 Campaign 不运行环境可配置的 Parser sidecar，而是对 package-owned、hash-verified 的两份
`file-analysis-v1` snapshot 执行现有严格解析与 Card replay，并要求 L1、零 ERROR/missing/warning/diagnostic。
valid-shape Unit 的 canonical 24 项 `tag_id + decision` 会按 Plan 输出，并以固定合成样例的
`source_role/unit_kind/unit_symbol` 标明归属；不输出源码路径、代码、Prompt、wire body、raw response、
模型 reason/evidence line、credential 或 state path。

CLI 在 summary 完整 graph 验证后，将相同安全投影以 `0600` 原子写入 state directory；
`ai-tag-campaign-live-smoke-summary-v2` 的 `summary_id` 对完整投影做内容寻址，可由
`load_campaign_run_summary(...)` 检测落盘后篡改。该文件明确
`result_artifact_scope=safe_summary_not_full_evidence_graph`：它没有保存 response receipt、Validation 的
完整 reason/evidence 或调用方 raw bytes，不能替代 Execution Result/evidence graph verifier。
Execution Result 本身是内部审计 artifact，仍含 `unit_id`，不得直接当作 metadata-safe 日志输出。该入口不接受
任意源码、任意 inspection JSON 或真实代码 Campaign。

合成/负向测试只证明上述状态矩阵、canonical 顺序、零 attempt、失败后继续、tamper rejection 与两层
verifier 及固定 synthetic CLI 的控制合同。2026-07-20 已对该 package-owned、hash-locked 的
4-ReviewUnit 合成 Campaign 执行一次真实 HTTP live smoke：4 个 Plan 各 attempt 一次、无重试，
4/4 均得到 24-Tag `valid_shape`。当前落盘文件只是
`safe_summary_not_full_evidence_graph`，固定 `not_qualified` 且不保留 raw response；它不是 formal
`AITagAnalysisResult/AITagExecutionOutcome`、不是 trusted-runner attestation，也不能作为
`HybridFeatureAnalysisResult` 或 Retrieval 输入。仓库仍没有任意真实项目代码 Campaign 的
multi-Unit live CLI，也没有人工 Truth、Tag P/R、重复运行稳定性、生产 prevalence、
provider/runner signature 或生产 qualification 证据。

### 11.8 当前 AI Tag Formal Execution V2 与受信消费边界

`ai-tag-analysis-result-v2`、`ai-tag-execution-outcome-v2`、
`ai-tag-trusted-execution-subject-v1`、`ai-tag-trusted-runner-attestation-v1` 和
`hybrid-feature-analysis-result-v2` 已实现为一条仅覆盖 **attempted Plan** 的 formal shadow
合同。公开的 authority 入口是 `DeepSeekFormalExecutionRunnerV2`：调用方只能提供 Plan、Claims、
一次性 capability 和 Envelope，不能注入 transport、run artifacts 或 raw response。该 runner 内部固定
构造仓库 HTTP/TLS transport，并由私有 verified sink 在同一次调用中接收已经完成运行图校验的 artifacts
与原始响应 bytes；随后才执行完整 upstream/run-artifact/raw-response rebuild，并生成确定性
Result/Outcome、签名 Subject、Hybrid V2 和完整 evidence bundle：

```text
deployment Gate trusted Plan inputs
+ Plan + Claims + one-shot capability + Envelope
-> fixed HTTP/TLS ShadowRunner
-> private verified artifacts/raw capture
-> deterministic full rebuild inside integrated runner
-> optional AITagAnalysisResultV2（仅 valid_result）
-> AITagExecutionOutcomeV2
-> AITagTrustedExecutionSubject
-> Ed25519 AITagTrustedRunnerAttestation
-> HybridFeatureAnalysisResultV2
-> externally pinned registry + full evidence verifier
-> opaque VerifiedAITagFormalExecutionEligibility
```

Result V2 和 Outcome V2 是 attempted-Plan evidence 的确定性投影；它们的 self-hash、strict loader 和
字段引用只能证明内容 identity/结构闭合，不能单独证明受信执行。`analysis_run_id` 确定性绑定
`plan_id + attempt_receipt_id`：同一 Plan 与同一 Attempt 被再次 formalize 时，Result、Outcome 和
run ID 必须保持相同，不能把重复签名计为第二次 provider attempt。新的
`formalization_event_id` 只存在于 Subject/attestation 的单次签名上下文，是 runner-signed nonce；
它既不是 provider run identity，也不是 provider occurrence 或外部时间权威证明。

provider evidence scope 按运行状态固定区分：valid Result V2 使用
`observed_over_tls_not_provider_signed`；Outcome/Subject 在有完整 HTTP response 时使用
`http_response_observed_over_tls_not_provider_signed` 并强制 raw-body full rebuild，timeout、transport
error 等没有完整 response 时使用 `fixed_tls_transport_attempt_no_complete_verified_response`，不得伪造响应
provenance。provider 本身没有签名。零 attempt 的 `skipped_budget/not_run` 仍只属于 11.7 Campaign
运行审计合同，不能生成一个 attempted Outcome V2 来冒充运行。

Subject 使用 runner-held Ed25519 key 签名；verifier 的 trust root 是部署侧显式 pin 的、不可序列化
registry，并检查 trust domain、active/revoked key、允许的 runner release、registry/policy fingerprint、
Subject 签名和全部确定性投影。只有完整
`AITagFormalExecutionEvidenceV2` 经 `AITagFormalExecutionVerifierV2` 验证后返回的不可序列化
`VerifiedAITagFormalExecutionEligibility` 才能授权下游消费 AI signal。独立 Result、Outcome、Subject、
attestation、Hybrid JSON，调用方布尔值，或 post-hoc caller-supplied artifacts/raw bytes，都不能替代该
eligibility；仓库不导出可将这些调用方输入提升为 authority 的 producer。完整 Evidence 是由集成 runner
私有 token 构造、普通属性不可变且没有 raw-body 公共 accessor 的进程内对象；registry、signer、runner、
verifier 和 eligibility 同样拒绝普通属性替换或删除。registry 每次验证会重算 pinned content identity；
eligibility 每次访问会重验 Result/Outcome/Hybrid identity binding，并从 Result 与 Hybrid 双侧重建
AI-positive Tag，不信任单独的缓存值。

该密码学合同仍有明确的未证明边界：

- upstream closure 从调用方提供的 roots 开始，只重建到 Card/Plan，不证明 Parser、ReviewUnit、源码或
  Git provenance；
- egress approval 与 budget reservation 仍是进程内 verifier/ledger 引用，不是外部 authority 或生产
  预算 attestation；
- `runner_release_fingerprint` 只是 pinned registry 的 allowlist claim，不是代码 Git、二进制或 remote
  attestation；
- signer-holding Python 进程本身仍是信任根；当前没有独立 signer service、进程完整性、KMS/HSM 或
  remote attestation，因而不能抵抗该受信进程自身通过反射、`object.__setattr__` 或 monkeypatch 绕过
  语言级封装；这里的 private/immutable 是普通 API fail-closed，不是安全沙箱；
- provider signature 不存在，所有 formal artifacts 仍固定
  `formal_use_scope=hybrid_retrieval_shadow_only`、`evidence_qualification_status=not_qualified`、
  `production_qualified=false`；
- `cryptography`/Ed25519 合同和 monkeypatched 固定 transport 合成测试通过，只证明集成 raw hand-off、
  rebuild 和签名合同，不证明真实 TLS 或 DeepSeek 身份；它也不等于部署 key provisioning、KMS/HSM、真实
  runner signer、正式 provider provenance、真实 Tag/文档质量或生产 qualification 已完成。

2026-07-20 固定 4-ReviewUnit synthetic Campaign 的历史 live 只保存了
`safe_summary_not_full_evidence_graph`；缺少 raw response bytes、完整 evidence graph 和当时的 runner
attestation。新 Formal V2 代码不能事后把该历史 summary 追认为 formal evidence。

## 12. 兼容 RetrievalQuery 与正式 Retrieval 输入

当前 `AnalysisResult.retrieval_query` 是早期 CLI 的 compatibility-only 视图。它仍保留
`RetrievalUnit.code_features/dimensions/routing_tags` 和 `MrContext.triggered_dimensions`，但必须
与 `FeatureRoutingResult` 对齐：

```text
compat tags              == profile.exact_tags
compat dimensions        == profile.dimensions
compat routing_tags      == profile.routing_tags
MR triggered_dimensions  == result.mr_dimensions
```

它没有表达 `retrieval_policy`、exact/hint signal scope、Draft shadow、activation trace 或 Question
bindings，因此不是在线 Retrieval 的正式输入。`build_retrieval_request(...)` 已冻结
`retrieval-request-v1`，只接收完整 `AnalysisResult + ContextPlanResult`；正式
`FeatureRoutingResult` 必须已经绑定在 `AnalysisResult` 中并可重放，且实现不读取兼容对象。

正式请求绑定 `request_id/context_plan_id/feature_routing_id/feature_config_version/index_version`、
目标平台和独立知识 token budget。每个 Unit 绑定 `source_ref_id/profile_id`、正式与可 dispatch
Review Questions、`unit_exact` facts、exact/routing Tags、retrieval/routing Dimensions、最小代码
摘录、确定性 intent、Parser/context quality 和 Unit 预算。请求对象会重放上游图并拒绝 identity、
scope、排序、配置或预算漂移；Dimension 不能绕过 Feature Routing 独立启动检索。

`retrieval-request-v2` 已实现为与 v1 无继承关系的独立 closed schema，并有 duplicate-key-safe
strict loader、内容寻址 identity 和确定性 structural Builder。V2 无损保留 v1 的正式
Profile、Review Question、Dimension、requested rules、intent 和预算守恒，并显式增加
`hybrid_analysis_id`、`import_uses`、`ai_inferred_tags`、`tag_disagreements`、
`candidate_dimension_ids` 和 `vector_query_policy`。Builder 先重建完整 v1 request 作为基线，再从
`AnalysisResult + ContextPlanResult + SourceSnapshot` 重建 Card，验证调用方提供的 ModelView、
Request/Outcome/Result 和 Hybrid 引用图后才做字段投影。只有 valid AI `positive` 进入
`ai_inferred_tags`；`not_supported/abstain` 不会删除 static 信号，candidate Dimension 不会绑定
专项 RQ 或改写 formal coverage。V2 code-first vector renderer 只消费代码摘录和 exact facts，
不把 static/AI Tag、Dimension 或 RQ 文本拼入 query。

若 Hybrid 图声称 `valid_result/invalid_output/unavailable` 等已经发生 provider attempt，Builder 还要求
显式使用 `analysis-card-builder-v2-provider-egress` Card policy；默认
`none_no_provider_dispatch` Card 不能与 attempted outcome 组合。该校验只排除结构矛盾，provider-egress
policy 本身不是外发批准；V2 Builder 也不消费 Formal V2 Subject/attestation，因此不能建立受信消费
authority。该缺口由下述 V3 gate 单独关闭。

这是**结构合同完成边界**，不是 V2 Retrieval runtime。标准 `RetrievalService` 仍只接受
`retrieval-request-v1`，Phase C 也没有新增 V2 Retriever。现有 live Campaign summary 和
`AITagResponseValidation` 固定是 non-formal 产物，不能直接填充 Builder 要求的 Hybrid 输入图。
v1 request 的 schema、loader、Query Planner、RetrievalService、Golden 和标准
`evidence-pack-v2` 执行行为保持不变。

`retrieval-request-v3` 进一步关闭了 V2 只验证结构引用、没有受信消费凭证的边界。V3 是与 v1/v2
无继承关系的独立 closed schema，除保留 v1 的正式字段与预算不变量外，还绑定
`formal_hybrid_analysis_id`、`formal_execution_outcome_id`、可选 `formal_ai_result_id`、
`trusted_execution_subject_id` 和 `trusted_runner_attestation_id`。strict loader 和 self-hash 仍只证明
序列化请求的结构与内容 identity，不授予执行权限。

`TrustedRetrievalRequestV3Builder` 必须由部署侧配置一个
`AITagFormalExecutionVerifierV2`，并要求 formal evidence mapping 精确覆盖全部 primary Units。Builder
会从 `AnalysisResult + ContextPlanResult + SourceSnapshot` 重新构造 v1 baseline 和 provider-egress
Cards，逐 Unit full-verify 完整 Formal V2 evidence，再核对 Card/source/profile/routing/context identities。
只有 verifier 返回的 valid Result V2 中 `positive` judgment 能投影到独立
`ai_inferred_tags`；signed `unavailable/invalid_output` 不携带 AI signal，AI negative 不删除 static
exact/routing Tag，AI signal 不进入 `exact_tags`、专项 RQ 或 formal Dimension coverage。V3 vector
renderer 仍只消费代码摘录和 exact facts，不拼入 Tag、Dimension、attestation 或 intent prose。

Builder 返回不可序列化的 `VerifiedRetrievalRequestV3`，同时持有结构化 V3 request、同次重建的完整
v1 baseline、V3 exact-facts 快照和逐 Unit opaque eligibility；wrapper 在构造及每次公开访问时重验
全部 v1 正式字段、包括 `import_uses` 在内的 V3 exact facts、proof identity 与 AI 派生字段。因此仅
反序列化 `retrieval-request-v3` JSON 不能绕过 formal verifier。标准
`RetrievalService` 仍只接受 `retrieval-request-v1`；独立的 `RetrievalShadowServiceV3` 则只接受
**exact type** `VerifiedRetrievalRequestV3`，裸 V3、V3 子类或反序列化 V3 JSON 都不是 Phase C
执行 authority。

### 12.1 当前 Phase C Retrieval shadow 合同

Phase C 已实现独立 `retrieval-shadow-policy-v1`。该 frozen、内容寻址 policy 必须绑定当前
`retrieval-config-v1` fingerprint、相同 `rrf_k/result_limit`，并按固定顺序配置五个相互独立的 pool：

```text
formal_exact    -> unit_exact
file_hint       -> file_hint
text_keyword    -> text_keyword
ai_inferred     -> ai_inferred
semantic_vector -> semantic
```

每个 pool 独立限制候选数量、保存连续 pool rank、来源 scope 和 RRF weight。默认 policy 的权重依次为
`1.0 / 0.5 / 0.25 / 0.25 / 1.0`，`rrf_k=60`、每 Unit `result_limit=8`；这些值是当前冻结的 shadow
合同，不是由真实质量校准得到的生产参数。`text_keyword` 不再冒充 `unit_exact`，AI positive 只能进入
`ai_inferred`；AI negative/not-supported 不删除 static 信号。向量 query 使用
`code-exact-facts-v1`，只包含代码摘录和 exact facts，不拼入 static/AI Tag、Dimension、RQ 或
attestation 文本。

`RetrievalShadowServiceV3.compare(...)` 先从 verified wrapper 取出同次重建的 v1 baseline，并调用标准
`RetrievalService` 生成原样的 v1 control `EvidencePack`。随后为同一 Unit 构造一次共享的五-pool
候选账本，并以相同 policy 做两条加权 RRF：

```text
static_vector = formal_exact + file_hint + text_keyword + semantic_vector
hybrid        = formal_exact + file_hint + text_keyword + ai_inferred + semantic_vector
```

两条 arm 都只用正式 `retrieval_dimension_ids` 计算 coverage 和 per-Unit budget；
`candidate_dimension_ids` 固定为 `diagnostic_only`，不能绑定专项 RQ，也不能进入 formal
requested/covered/uncovered Dimension。每条 Clause 保留逐 pool contribution 和完整 `matched_by`，因此
AI-only 命中不能变成 `unit_exact`。applicability exclusion、authority、结果数量和真实知识 token 预算仍
逐 Unit 执行。

Phase C 的实验 arm 不生成新的 `evidence-pack-v3`，也不冒充现有 `evidence-pack-v2`；同次 v1
control 仍由标准 `RetrievalService` 生成 `evidence-pack-v2`。实验部分输出两层不同信任语义的对象：

1. `retrieval-shadow-result-v1`：closed、内容寻址、可序列化的审计产物，绑定 verified V3 request、v1
   control request/EvidencePack、base config、shadow policy、index/build/source bundle、embedding、Formal
   attestation 和逐 Unit 五 pool/两 arm。它固定：

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

2. `VerifiedRetrievalShadowResultV3`：仅由 `RetrievalShadowServiceV3` 私有 construction token 构造的
   不可序列化 runtime wrapper。它同时持有 verified V3 authority、shadow artifact 和 v1 control
   EvidencePack；还保留与公开返回对象相互独立的完整 shadow/control construction snapshots。每次公开
   访问都要求当前 artifact/control 与对应 snapshot 精确一致，并重验 request/control、构造时快照的
   KnowledgeIndex/base config/shadow policy 及逐 Unit Formal/Tag/Dimension/pool/arm binding。
   反序列化 `retrieval-shadow-result-v1` 只能恢复 `serialized_audit_only` artifact，不能恢复 wrapper 或
   `runtime_verified` authority。

该 wrapper 与 Formal V2 eligibility 使用相同的进程内信任边界：它能拒绝普通属性改写、单根替换和
serialized artifact 提权，并会把 structured pools 与保留的 index/request/config/policy 重建结果对照，
同时逐字段核对所有 ranked Clause 的 KnowledgeIndex 内容。独立 construction snapshot 还绑定 semantic
path score、候选完整性、diagnostics/degraded 和标准 v1 control EvidencePack 的完整内容；公开访问不会
再次调用 embedding provider。它不是抵抗同一受信 Python 进程任意反射并同时替换全部内存 trust roots
的安全沙箱。

`KnowledgeIndex.origin=publication` 只改变 index/build provenance，并要求 Shadow Clause 为
`Baselined`；它不会改写上述资格常量。也就是说，即使调用方注入合法 publication index，Phase C 结果
仍是 shadow、not-qualified、非用户可见、不可进入 Prompt/Finding。当前仓库也仍没有真实生产
`PublishedKnowledgeBuild`、真实且独立复核并 sealed 的文档 Truth 或真实 Retrieval Precision/Recall，
因而该运行合同不构成生产质量证明。

### 12.2 当前 Phase D0 文档 Truth 与双臂评分合同

Phase D0 已实现独立的 `retrieval-document-truth-v1` 和
`retrieval-shadow-evaluation-v1`。Truth 逐 ReviewUnit 绑定 `source_ref_id/profile_id`、固定
KnowledgeIndex/build/source bundle、Feature Config、目标平台、development/calibration split 和供人工标注
的 critical Dimensions；每条 Clause 只能选择一个 `required/acceptable/irrelevant/forbidden` 标签，并保存
`SourceRef`、heading、`rule_type` 与 applicability。索引内 Truth 的来源、heading、applicability 和
`rule_type` 必须与冻结 Index 一致；只有 `required` 允许不在 Index，用于显式计算 Knowledge
coverage gap。同一 Unit 不能用不同 `rule_id` 或元数据重复计数同一 source locator；嵌套
来源与适用性字段也拒绝弱类型和未清理文本。该 schema 约束标签形状和内容 identity，不证明标签确由
人工产生或已形成共识。split 值同样由调用方提供；v1 没有独立的
rule-family/leakage-component 字段或 family seal，因此“分别聚合”不等于已经证明两个 split 无近重复泄漏。

评分器同时消费 self-hash 有效的 Truth、`RetrievalRequestV3`、`RetrievalShadowResultV3` 和
`KnowledgeIndex`，要求四个 root 的 Index、build、source bundle、Feature Config、target、Retrieval config、Unit
budget/source/profile 及 Formal/Tag/Dimension identities 完整一致。任一预算前或预算后实际出现的
Clause 都必须有唯一 Truth 标签；
未标注候选会 fail-closed，不会被静默当作 `irrelevant`。评分器不重新执行 Retrieval、embedding 或
DeepSeek，而是直接使用同一 shadow result 中：

```text
post_fusion_pre_budget = arm.ranked_clauses
post_budget_selected   = arm.selected_rule_ids
```

两个空间分别按固定 `K=1/3/5/8` 报告 Retriever required Recall、Full-chain required coverage、
Precision、Truth-critical Dimension coverage 和 required MRR，并单列 forbidden/applicability violation、
empty、token utilization、Index 外 required gap、hybrid 新增/挤出文档和 paired metric/MRR delta。聚合
使用 Clause micro counts，MRR 使用有对应 required 分母的 Unit mean；零分母为 `null`，true-negative
空结果不会伪造 `Precision=1.0`。Index 外 required 只进入 Full-chain miss 与 Knowledge gap，不会误算成
Retriever FN。

报告同时保留全体 Unit 的 observational arm aggregates，并对实际存在的 `development`、`calibration`
分别生成独立 split aggregates；不得用跨 split 总表替代 calibration 结论。dependency degraded 或 Formal
execution 非 valid 的原始观测仍保留，但 aggregate paired quality metric/MRR delta 与新增/挤出计数只消费
`comparison_eligible=true` 的 Unit；没有 eligible Unit 时 quality delta 为 `null`。forbidden/applicability
属于 hard-safety observation，预算前和预算后 delta 均从全部 Unit 计算，不因 degraded/invalid 被隐藏。

报告固定：

```text
evaluation_scope=relative_gain_on_fixed_index
verification_root_scope=caller_supplied_self_hashed_truth_request_result_and_index
authority_status=serialized_audit_only
downstream_use=offline_relative_evaluation_only
evidence_qualification_status=not_qualified
production_qualified=false
user_visible=false
prompt_eligible=false
finding_evidence_eligible=false
```

report loader/self-hash 只恢复内部可重建的 `serialized_audit_only` 审计 artifact；full verifier 会相对
调用方提供的四个 self-hash roots 完整重建并精确比较，但不会重新执行 Retrieval，也不证明 Shadow
Result 的 runtime authority 或 policy execution semantics。报告固定保留 acceptance holdout、Truth seal、
生产知识质量、production prevalence、policy root 和 runtime authority 六类 qualification blockers。
当前没有真实人工 Clause Truth、双审/consensus/Git seal、acceptance holdout 或生产知识，因此 D0 只证明
评分合同和合成边界，不能输出真实业务 P/R 或 production qualification。

## 13. EvidencePack

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
  "degraded": false,
  "embedding_version": "candidate-model@internal-v1",
  "units": [
    {
      "unit_id": "...",
      "profile_id": "feature-profile:sha256:...",
      "requested_dimension_ids": ["DIM-05", "DIM-06"],
      "routing_dimension_ids": ["DIM-05", "DIM-06"],
      "covered_dimension_ids": ["DIM-05", "DIM-06"],
      "clauses": [
        {
          "rank": 1,
          "rule_id": "RESOURCE/TIMER/R-01",
          "rule_type": "constraint",
          "text": "组件创建的定时器应在不再使用时主动清理。",
          "status": "Baselined",
          "heading_path": ["资源管理", "定时器"],
          "parent_context": "组件资源应与生命周期配对。",
          "dimension_ids": ["DIM-05", "DIM-06"],
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
      "uncovered_dimension_ids": [],
      "diagnostics": []
    }
  ],
  "diagnostics": []
}
```

这里的 `EvidencePack` 专指标准 `evidence-pack-v2`，不是 Phase C shadow artifact。它逐 Unit 划分
requested/covered/uncovered Dimensions。publication 与 golden fixture 只包含 Baselined Clause；显式
evaluation fixture 可包含 Draft Clause，但固定 `production_eligible=false`。EvidencePack 同时保存
match scope、适用性、exact/vector rank、RRF、authority、token 和结构化 diagnostic。
`dimension_ids` 是多值。调试字段记录到评审审计数据，但不全部进入 Prompt。

## 14. RuleFinding

```jsonc
{
  "rule_id": "ARKTS-NO-ANY",
  "unit_id": "...",
  "file": "PhotoWall.ets",
  "line": 18,
  "severity": "high",
  "problem": "使用了 ArkTS 禁止的 any 类型",
  "code_evidence": "const value: any = ...",
  "reference_rule_ids": ["LANGUAGE/TYPE/R-01"],
  "confidence": "deterministic"
}
```

## 15. Final LLM ReviewRequest

每个 Unit 逻辑上独立评审，物理上可以在 token budget 内批量发送：

```jsonc
{
  "review_mode": "diff",
  "unit": {},
  "dimensions": [],
  "evidence": [],
  "deterministic_findings": [],
  "output_schema_version": "finding-v1",
  "prompt_version": "review-v1"
}
```

## 16. Finding

```jsonc
{
  "finding_id": "...",
  "unit_id": "...",
  "file": "PhotoWall.ets",
  "start_line": 17,
  "end_line": 17,
  "dimension_id": "DIM-06",
  "severity": "high",
  "title": "定时器可能重复创建且未确认释放",
  "problem": "...",
  "code_evidence": "...",
  "impact": "...",
  "recommendation": "...",
  "references": ["RESOURCE/TIMER/R-01"],
  "confidence": "medium",
  "is_diff_related": true,
  "origin": "llm"
}
```

`severity`：

```text
critical | high | medium | low | suggestion
```

`origin`：

```text
rule | llm | merged
```

## 17. ContextRequest

当上下文不足时，Final LLM 不得猜测，应返回：

```jsonc
{
  "symbol": "PhotoWall.aboutToDisappear",
  "reason": "需要确认定时器是否调用 clearInterval",
  "required_for_dimension": "DIM-06"
}
```

编排层最多执行受限次数的上下文补充，避免无限循环。

## 18. ReviewReport

```jsonc
{
  "review_id": "...",
  "change_id": "...",
  "status": "completed",
  "findings": [],
  "summary": {},
  "versions": {
    "parser": "...",
    "source_bundle": "...",
    "feature_config": "...",
    "rule_registry": "...",
    "index": "...",
    "embedding": "...",
    "prompt": "...",
    "model": "..."
  },
  "diagnostics": []
}
```

## 19. Finding 校验不变量

- `file` 必须属于本次 ChangeSet。
- `line` 必须能映射到新文件或明确标记为 deleted-line comment。
- `references` 必须存在于本次 Unit Evidence 或 RuleFinding 中。
- `critical/high` 必须有代码证据和规范/规则依据。
- `suggestion` 可以无规范依据，但必须标记为建议。
- `is_diff_related=false` 默认不回写行内评论。
- 相同文件、行、规则和问题类型的 Finding 必须去重。
