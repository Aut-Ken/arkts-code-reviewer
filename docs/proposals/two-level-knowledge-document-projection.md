---
title: 一级原始文档到二级检索文档的投影与双模型审核架构提案
status: proposal
implementation: partial
decision: accepted_for_implementation
updated: 2026-07-23
---

# 一级原始文档到二级检索文档的投影与双模型审核架构提案

## 0. 先看结论

本提案要解决的问题很直接：原始 Markdown 通常是按照“作者怎样讲清楚一项技术”组织的，并不是
按照“代码评审系统怎样快速找到限制、例外、API 和失败行为”组织的。直接搜索原文容易受文档结构
影响；把原文压缩成一张 Document Card，又会遗漏细节。

目标方案是：

```text
一级文档：不可修改的原始 Markdown 真值
        ↓
确定性解析：把原文变成可寻址、可校验的 Source Atom
        ↓
确定性 Fragment：在大 Atom 内建立 UTF-8 精确地址，不改写原文
        ↓
DeepSeek：组合 Fragment 为多标签 Facet，并描述上下文和关系
        ↓
确定性编译器：生成“多分类索引 + 单份原文单元库”的二级 Markdown
        ↓
机械校验：证明没有原文篡改、未知引用或 Atom 遗漏
        ↓
Grok：独立审核分类、标题、上下文和关键事实覆盖
        ↓
通过审核的二级文档进入实验检索库
        ↓
检索命中二级文档后，最终引用仍回到一级文档
```

一句话概括：

> DeepSeek 负责整理，程序负责搬运，Grok 负责检查，一级文档负责最终真值。

二级文档不是 AI 重写的“新规范”，而是一级文档的可重建检索视图。同一 Fragment 可以进入多个
Facet，同一 Facet 可以组合多个 Atom 的 Fragment；规范正文仍只保存一次，避免重复内容把检索
分数虚高。

---

## 1. 文档状态与事实边界

本文是已获用户认可、正在分阶段实现的目标架构提案，不是可以覆盖运行事实的 canonical 合同。
当前真值仍以 `docs/architecture/`、`docs/modules/`、配置和代码为准。

### 1.1 当前已经实现

仓库现有能力包括：

- `SourceRegistry`、固定 Git revision 和 `SourceRef`；
- 从固定 Git object 读取并生成 `NormalizedDocument`；
- 静态 `MarkdownDocumentMap`，保存标题层级、行号、父子关系和内容哈希；
- `DocumentCardRequest`、`DocumentCardDispatchPlan`、受控 DeepSeek 单文档和多文档运行；
- `DocumentCard` 与 `DocumentCatalogBuild` 的导航-only、非 Evidence 合同；
- 7 篇固定 Markdown 的真实 DeepSeek Campaign，7/7 为 `valid_card`；
- `SourceAtomSet`：在静态章节边界内建立段落、列表、表格、代码块等可重建原文单元，并让 Atom
  与 Region 无遗漏、无重叠地覆盖全部物理行；
- `SourceFragmentSet`：在每个 Atom 内建立 UTF-8 byte 半开区间的精确 Fragment 分区，普通 prose
  使用固定句末、换行和有界 fallback，表格、代码块等保持 whole-Atom；每个 Atom 的全部字节必须
  无 gap、无 overlap 地覆盖，Fragment 不保存或改写正文；
- `SemanticFacetSet` 与 `SemanticRelationGraph`：接受调用方提供的 Draft，允许同一 Fragment
  多 Facet、每个 Facet 多闭合分类、跨 Atom Fragment 组合、显式 Context Signature 和同文档 typed
  relation；所有产物固定非 Evidence、未语义审核；
- Facet 与 Projection v1 的 legacy adapter：可在语义可表达时把现有 Atom Mapping 展开为
  Fragment Facet，也可将 Facet 按 parent Atom/category 有损折叠给现有 L2 renderer；旧 renderer
  和 PostgreSQL schema 不变，不可表达、可能提升 unknown 或会合并不同 Facet 时 fail-closed；
- `DocumentProjectionMapping`、`ProjectionManifest` 和 `DocumentProjectionRecord`：接受封闭的
  Atom 多对多分类 Mapping，由确定性编译器生成“多分类链接 + 单份原文单元库”的 L2 Markdown；
- 机械 verifier：从 L1、Map、AtomSet 和 Mapping 完整重建 L2，拒绝未知 Atom、漏覆盖、正文
  突变、重复 canonical binding 和 identity 漂移；
- 独立的 PostgreSQL `document_projection` schema、checksum migration runner 和两阶段不可变
  Store；已通过真实临时 PostgreSQL 的 migration、写入、封存、读取、幂等 round-trip，并验证
  封存后不能追加 Atom/Binding；
- Clause 候选、Knowledge annotation、双审、curation 和 publication 的现有独立合同；
- Clause 级 Exact、Vector、RRF、applicability 和 Evidence budget 检索骨架。

### 1.2 当前尚未实现

本提案中的以下能力目前仍不存在：

- DeepSeek Fragment-to-Facet Prompt、Request/Plan、响应 reducer 和受控 runner；
- Grok 二级文档审核 packet、receipt 和 correction loop；
- 跨文档 Topic/Context 聚合与关系图；
- 通过审核的实验 `L2ProjectionBuild`；
- 二级文档 Catalog、全文索引或真实检索 runtime；
- ReviewUnit 到二级文档的真实 Recall@K、Precision@K 或最终答案质量结果。

当前 Atom Mapping 和 Semantic Facet/Relation Draft 都只能由调用方提供并经过本地严格封装；
尚无 DeepSeek 生成结果。数据库仍只保存 Projection v1，状态固定为
`mechanically_verified`，资格固定为
`mechanically_verified_projection_not_semantically_reviewed`；它不表示 DeepSeek 已分类、Grok
已审核或检索质量已经合格。

### 1.3 现有授权不能自动复用

2026-07-22 的 7 文档 DeepSeek Campaign 只批准了固定 Document Card Prompt、固定请求正文和固定
预算。它不批准本提案的新映射 Prompt、二级文档生成请求、Grok 审核请求或未来重新执行。

现有 Grok Knowledge Review 外发策略也只覆盖既有 Clause 审核合同，不自动覆盖二级文档审核。
任何真实模型调用仍需要新的精确配置、inspection 和用户批准。

### 1.4 当前事实和目标状态不要混淆

| 能力 | 当前事实 | 本提案目标 |
|---|---|---|
| 原始文档登记与固定 revision | 已实现 | 直接复用 |
| Markdown 标题树和行号映射 | 已实现 | 作为 Atom 的外层结构边界 |
| DeepSeek Document Card | 已实现并跑过 7 文档 Campaign | 保留为辅助导航，不承担完整覆盖 |
| Source Atom | 已实现并通过合成合同测试 | 确定性建立原文地址 |
| Source Fragment | 已实现 UTF-8 精确分区与严格重建 | 作为 Atom 内部地址，不独立成为 Evidence |
| Semantic Facet/Context/同文档 Relation | 调用方 Draft reducer、identity、覆盖门禁与 fail-closed legacy adapter 已实现 | DeepSeek 生成、Grok 审核后用于 Shadow 导航 |
| L1 到 L2 分类映射 | closed Mapping 与本地封装已实现；DeepSeek 生成未实现 | DeepSeek 输出 closed JSON Mapping |
| L2 Markdown | 确定性编译和机械 verifier 已实现 | 程序确定性编译，不让模型自由改写 |
| L2 Shadow 数据库 | 独立 migration、不可变 Store 和真实 PostgreSQL round-trip 已实现 | 后续只接收明确状态的审核产物 |
| L2 Grok 审核 | 未实现 | 只做独立机器审核，不冒充 Truth |
| L2 文档检索 | 未实现 | 先进入隔离 shadow runtime |
| 真实代码到文档质量 | 未证明 | 用 must-have Document Truth 评测 |

---

## 2. 为什么要引入二级文档

### 2.1 原始 Markdown 的组织目标不同

以 TaskPool 文档为例，原文为了方便开发者阅读，把很多内容放在一个“TaskPool注意事项”章节中：

- `LongTask` 例外；
- CPU 执行不能超过 3 分钟；
- `cpuDuration` 和 `ioDuration`；
- `setTransferList()` 和 `setCloneList()`；
- 16MB 序列化限制；
- `IDLE` 优先级；
- Promise 跨线程限制；
- TaskPool 工作线程不能使用 `AppStorage`；
- 不能指定执行线程，需要时改用 Worker。

这些内容对于人类阅读是一组“注意事项”，对于代码检索却属于多个不同入口：

```text
并发限制
数值限制
数据传输
禁止事项
状态管理
API 符号
例外条件
替代方案
```

原始文档没有错，只是它的结构并非为代码检索设计。

### 2.2 Document Card 是有损压缩

当前真实 Campaign 中，TaskPool Card 把大量注意事项压缩成了“任务时长限制、序列化支持”等
宽泛描述，只保留 6 个 API 提示。运行没有失败，完整正文也没有被截断；遗漏的主要原因是当前
Prompt 和 Schema 本来就只要求简短导航摘要。

因此：

```text
valid_card
≠ 规则完整
≠ API 提示完整
≠ 检索不会漏文档
```

### 2.3 直接让 AI 重写原文也不安全

如果让 DeepSeek 自由“重新写一篇更适合检索的 Markdown”，模型可能：

- 把“建议”改成“必须”；
- 合并掉例外条件；
- 改写数值或适用范围；
- 删除它认为不重要的内容；
- 把模型解释混进来源事实；
- 生成无法映射回原文的句子。

所以二级文档不能是自由改写，必须是受约束的投影。

---

## 3. 通俗类比

可以把一级文档看成一本原版教材。

二级文档不是重新写一本教材，而是给原文段落编号，再建立多个专题索引：

```text
原版教材
├── 原来的章节顺序
├── 原来的句子
└── 原来的例子

检索版目录
├── “禁止事项” → 原文第 18、26、31 段
├── “数值限制” → 原文第 12、18 段
├── “API符号”   → 原文第 7、12、22 段
├── “例外条件”  → 原文第 18、29 段
└── 原文单元库：每段正文只保存一份，并标明原始页码
```

DeepSeek 像资料整理员，只填写“这段材料应该被哪些目录引用”。程序按照编号建立目录和原文单元库，
避免整理员偷偷改字。Grok 像复核员，检查是否分错类、标题是否误导、上下文是否被拆坏。

---

## 4. 设计目标与非目标

### 4.1 目标

1. 把面向人阅读的 Markdown 转成更适合代码检索的文档视图。
2. 保证二级文档中的来源正文逐字来自一级 `NormalizedDocument.body`。
3. 支持同一段原文被多个检索分类引用，但 canonical 正文只保存一次。
4. 保证所有可检索 Source Atom 都进入二级文档的原文单元库，并至少有一个分类或
   `unclassified_atom_ids` 入口。
5. 保留一级文档 revision、路径、行号、标题路径和内容哈希。
6. 让 DeepSeek 负责语义整理，但不能修改来源正文。
7. 让 Grok 独立审核分类质量，但不能直接修改二级文档。
8. 让检索优先使用二级文档，最终规范引用仍回到一级文档。
9. 明确区分机械完整性、模型审核通过和真实检索质量。

### 4.2 非目标

本提案第一版不负责：

- 把二级文档直接变成 `Baselined` Clause；
- 绕过人工 curation 或现有 Knowledge publication；
- 把 Grok 判断当成人工 Truth；
- 把二级文档直接当成 Finding evidence；
- 对原始 Markdown 做固定 token 长度的向量切块；
- 修改现有 Retrieval v1 或现有生产资格边界；
- 解决 Rules、Final LLM、Finding 或 GitCode 发布；
- 宣称生成成功就等于真实检索质量合格。

---

## 5. 核心术语

### 5.1 一级文档：L1 Source Document

一级文档是固定来源、固定 revision 下的原始知识真值。

在当前代码中，一级处理入口是 `NormalizedDocument`。需要注意：Adapter 会把 UTF-8 BOM 和
CRLF/CR 换行规范化为 UTF-8 文本和 LF。因此本提案中的“逐字复制”指逐字复制
`NormalizedDocument.body`；同时通过 `SourceRef.content_hash` 继续绑定原始 Git bytes。

一级文档必须绑定：

```text
document_id
source_id
revision
relative_path
raw source content_hash
normalized_body_hash
adapter_version
```

### 5.2 Source Atom

Source Atom 是一级文档中最小的“可搬运但不可修改”的结构单元。

它不是传统 RAG Chunk，不按 500 tokens、1000 字符等固定大小切分，也不被当成独立规范。它只给
原文的自然 Markdown 结构分配稳定地址。

第一版支持的 Atom 类型建议包括：

```text
paragraph
list_item
table
code_block
blockquote_or_note
link_or_footnote_definition
raw_block
```

标题不必作为来源正文 Atom；它作为每个 Atom 的 `heading_path` 上下文保存。空行、HTML 注释、
front matter 等不参与检索的区域也必须被记录为 non-content region，不能静默消失。

### 5.3 DeepSeek Mapping Plan

DeepSeek Mapping Plan 只描述：

```text
哪些 Atom
属于哪些检索分类
需要哪些上下文 Atom
有哪些搜索别名
```

它不携带可自由改写的来源正文，也不直接生成二级 Markdown。

### 5.4 二级文档：L2 Retrieval Projection

二级文档是由确定性程序根据 Mapping Plan 生成的检索视图。

它允许增加：

- 生成的分类标题；
- 检索别名；
- Atom 来源注释；
- 一级文档链接；
- 多视图目录。

它不允许改写 Source Atom 正文。canonical L2 由两部分组成：

```text
检索目录：一个 Atom 可被多个分类引用
原文单元库：每个 Atom 的正文只出现一次，并保持 L1 原始顺序
```

JSON Mapping/Manifest 是机器真值，Markdown 是由它们确定性生成的人类和模型可读视图。

### 5.5 Grok Review Receipt

Grok Review Receipt 是 Grok 对一级文档、Atom、Mapping 和二级文档的独立审核结果。它可以要求
修改分类，但不能直接替换 Mapping 或编辑二级 Markdown。

### 5.6 L2 Projection Build

L2 Projection Build 是一组通过机械校验和指定 Grok 审核门禁的二级文档集合。它仍然是：

```text
machine_reviewed_navigation
evidence_eligible = false
production_qualified = false
```

---

## 6. 总体架构

### 6.1 主数据流

```text
Source Registry + pinned Git object
                │
                ▼
        NormalizedDocument (L1)
                │
                ├───────────────┐
                ▼               │
       MarkdownDocumentMap      │  当前已实现
                │               │
                ▼               │
          SourceAtomSet         │  本提案新增
                │               │
                ▼               │
      DeepSeek Mapping Request  │
                │               │
                ▼               │
       DocumentMappingPlan      │
                │               │
                ▼               │
   Deterministic L2 Compiler ───┘
                │                  只从 L1 复制正文
                ▼
     L2 Retrieval Projection
                │
                ├── Mechanical Verifier
                │     coverage / hashes / references / rebuild
                │
                ▼
        Grok Review Packet
                │
                ▼
        Grok Review Receipt
                │
        ┌───────┴──────────────┐
        │                      │
  needs_correction     accepted_for_shadow
        │                      │
        ▼                      ▼
DeepSeek full remap       L2 Projection Build
        │                │
        └──── rebuild ───┤
                         ▼
                 Document Retrieval
                         │
                         ▼
                 resolve back to L1
```

### 6.2 实验检索通道与正式 Evidence 通道

二级文档先解决“找到哪篇文档”的问题，不能绕过现有 Clause 发布链直接成为 Evidence。两条通道
必须并行保留：

```text
实验文档检索通道
L1 → Atom → DeepSeek Mapping → L2 → Grok Review
   → DocumentRetrievalCandidateSet → DocumentSelectionResult
   → resolve 回 L1 原文
   作用：扩大候选文档召回；固定非 Evidence

正式知识证据通道
L1 SourceSpan → ClauseCandidate → Annotation → 双审 Consensus
   → 人工 Curation → PublishedKnowledgeBuild → KnowledgeIndex
   → EvidencePack
   作用：给后续 Finding 提供正式知识证据
```

第一版不把 `DocumentSelectionResult` 塞进当前 `EvidencePack`，也不把 L2 分类标题伪装成 Clause。

### 6.3 与当前项目匹配的技术栈

| 职责 | 技术选择 | 原因 |
|---|---|---|
| Markdown 结构解析 | Python + `markdown-it-py` 3.x | 项目已经依赖；可按 Markdown block 结构切分，不按 token 硬切 |
| 数据合同 | Pydantic v2 frozen models | 延续现有 closed schema、字段校验和不可变产物风格 |
| 身份与重建 | canonical JSON + SHA-256 | 延续现有内容寻址 ID，保证同输入得到同 identity |
| DeepSeek 调用 | 现有 HTTPX 受控调用模式 | 复用 Plan、body hash、预算、超时、一次性批准和无重试边界 |
| Grok 审核 | 固定 Prompt + JSON Schema + 受控 CLI/adapter | 复用现有 Grok packet/receipt 思路，但新建 L2 专用合同 |
| 确定性存储 | UTF-8 JSON + Markdown artifact directory | 便于人检查、机器重建和 Git/离线归档 |
| 检索实验 | 文档级倒排、符号索引、分类边和可选文档级向量 | 第一版先找文档，不返回无父文档的孤立 chunk |
| 测试 | pytest + fixture + Golden + live Campaign artifact | 分开证明合同、真实模型运行和真实检索质量 |

---

## 7. 角色与权限边界

| 角色 | 可以做什么 | 不可以做什么 |
|---|---|---|
| 一级文档 | 提供最终来源真值 | 被模型在线修改 |
| Source Atom Builder | 确定性识别原文结构、行号和哈希 | 判断业务语义、创建规范 |
| Source Fragment Builder | 为 Atom 内原文建立 UTF-8 精确地址和完整覆盖 | 判断组件、原则、场景或关系 |
| DeepSeek | 组合已有 Fragment 为多标签 Facet，生成非证据型上下文和别名 | 决定物理切片、改写或删除原文、批准发布、生成 Finding |
| L2 Compiler | 生成多分类链接，并按 Atom ID 逐字复制一份原文单元库 | 自己补充来源事实、为分类重复堆叠正文 |
| Mechanical Verifier | 检查覆盖、哈希、引用、顺序和确定性重建 | 判断分类语义是否合理 |
| Grok | 独立检查分类、标题、上下文和关键内容覆盖 | 直接编辑 Mapping、直接修改二级正文、替代真实检索评测 |
| L2 Retrieval | 使用二级文档扩大候选文档召回 | 把二级生成标题当作规范证据 |
| Final Review | 读取命中材料并形成后续判断 | 只凭未映射的 AI 摘要声称规范结论 |

最重要的信任方向是：

```text
L2 可以指向 L1
L2 不能覆盖 L1
Grok 可以拒绝 L2
Grok 不能把自己的解释写成 L1
```

---

## 8. Source Atom 设计

### 8.1 为什么需要 Atom

“把原文重新归类但一字不改”包含一个客观矛盾：只要重新排列内容，就必须知道究竟移动哪一段。
Source Atom 用来解决这个问题。

Atom 是地址单位，不是检索最终返回的孤立碎片。它必须始终依附于：

```text
document_id
heading_path
source_span
original ordinal
context relations
```

### 8.2 切分原则

第一版以当前 `MarkdownDocumentMap.sections[*].content_span` 作为外层章节边界，再使用 Markdown AST
在章节内部建立 block 级 Atom，不采用 token window，也不做语义猜测式切分：

1. 普通段落作为一个 Atom。
2. 每个顶层列表项连同其嵌套子列表作为一个 Atom；嵌套子项不再建立重叠 Atom。
3. 表格整体作为一个 Atom，不能按行随意拆开。
4. fenced code block 整体作为一个 Atom。
5. note/warning block 与其正文整体保留。
6. 链接定义和脚注定义建立独立 Atom，并由使用它们的正文记录依赖关系。
7. 一段说明紧接一个代码示例时，建立 `explains/example_of` 关系。
8. “上述”“以下”“该限制”等依赖前文的 Atom，必须记录 `required_context_atom_ids`。
9. 无法可靠解析的区域进入 `raw_block`，不允许丢弃。

所有内容 Atom 的物理行 span 必须互不重叠；标题、空行和 non-content region 与内容 Atom 一起
完整解释 L1 的每一行。关系可以多对多，但正文所有权只能属于一个 Atom。

这样既复用当前已经实现的标题树和行号合同，也避免把 TaskPool 的整个“注意事项”大章节当成一个
过粗单元。Atom Builder 只识别 Markdown 物理结构；它不能根据“这句话看起来像两条规则”再调用
模型拆句。

### 8.3 Atom 建议合同

```jsonc
{
  "schema_version": "source-atom-set-v1",
  "atom_set_id": "source-atom-set:sha256:...",
  "document_id": "openharmony-docs:.../taskpool-introduction.md",
  "source_ref": { "...": "现有 SourceRef" },
  "normalized_body_hash": "sha256:...",
  "document_map_id": "markdown-document-map:sha256:...",
  "builder_version": "markdown-source-atom-v1",
  "atoms": [
    {
      "atom_id": "source-atom:sha256:...",
      "ordinal": 12,
      "kind": "list_item",
      "heading_path": ["TaskPool简介", "TaskPool注意事项"],
      "source_span": { "start_line": 25, "end_line": 25 },
      "text_hash": "sha256:...",
      "required_context_atom_ids": [],
      "relation_ids": []
    }
  ],
  "regions": [
    {
      "kind": "non_content_html_comment",
      "source_span": { "start_line": 2, "end_line": 7 },
      "text_hash": "sha256:..."
    }
  ]
}
```

Atom JSON 可以不重复保存正文，只保存 span 和 hash。Verifier 始终从调用方提供的可信
`NormalizedDocument` 重新切片，不能仅信任 Atom 自报文本。

### 8.4 完整性要求

必须同时报告两种覆盖：

```text
physical_line_coverage = 100%
eligible_atom_mapping_coverage = 100%
```

- physical coverage 证明一级正文每一行都被内容 Atom 或显式 non-content region 解释；
- mapping coverage 证明每个可检索 Atom 至少进入一个正常 binding 或明确的未分类兜底集合。

两项达到 100% 只证明“都处理到了”，不证明“分类有用”。报告还必须给出
`unclassified_atom_rate`、每类 Atom 数、跨分类 Atom 比例和后续真实检索结果。

### 8.5 当前已实现的 Atom 内部 Fragment sidecar

Atom 继续作为原文所有权和 L1 回切边界；Fragment 只解决“大 Atom 内怎样精确指向不同句子或
局部范围”，不取代 Atom。当前 `source-fragment-set-v1` 使用
`utf8_byte_within_atom_half_open`：

```text
paragraph / list_item / blockquote / note
  -> 强句末符与换行边界
  -> 超过 800 codepoints 时使用确定性有界 fallback

table / code_block / raw_block
  -> whole-Atom Fragment
```

每个 Fragment 保存 `atom_id`、Atom/Fragment ordinal、Atom 内 UTF-8 byte span、Atom byte length
和 `text_hash`，不保存正文。每个 Atom 至少一个 Fragment，所有 span 必须从 0 连续覆盖到 Atom
末尾；UTF-8 非字符边界、gap、overlap、空 Fragment、跨 Atom 和重排全部拒绝。Builder/Verifier
始终从可信 L1 与 AtomSet 完整重建，因此 self-hash 不能掩盖切片漂移。

### 8.6 当前已实现的 Semantic Facet sidecar

`semantic-facet-set-v1` 允许模型未来表达：

```text
一个 Fragment -> 多个 Facet
一个 Facet -> 多个 primary Fragment
一个 Facet -> 多个闭合 CategoryKind
一个 Facet -> required context Fragment
```

Context Signature 固定包含 subject、component、role、scenario、operation、condition 和 version
字段，用于区分“TaskPool 工作线程”“Worker 独立线程”和“UI 主线程”等同主题不同语境。Facet
生成 identity 后，独立 `semantic-relation-graph-v1` 才能引用 Facet ID，避免
`Facet -> Relation -> Facet` 的 identity 环。当前关系只允许同文档的补充、例外、前置、示例、
替代、对比、表面冲突和同主题不同上下文。

所有 Fragment 必须作为 primary 进入至少一个 Facet，或明确进入
`unclassified_fragment_ids`；只被引用为 required context 不算完成分类。机械门禁只能证明地址、
覆盖和 identity 正确，不能证明主题、上下文或关系的语义正确。当前没有真实 DeepSeek/Grok Facet
结果。

---

## 9. DeepSeek 多对多分类映射

### 9.1 DeepSeek 输入

单篇请求包含：

- 一份由完整一级 Markdown 确定性生成的 `L1ProjectionModelView`；
- Document Map；
- Source Atom 的 ID、类型、标题路径和行号，以及每个 Atom 内的固定 Fragment ID/byte span；
- 冻结的分类合同；
- 输出 JSON Schema；
- 明确的“禁止改写、禁止遗漏、只能组合已有 Fragment、一个 Fragment 可进入多个 Facet、无法判断
  进入 `unclassified_fragment_ids`”指令。

DeepSeek 不读取现有检索结果，不读取 Grok 审核结果的历史答案，也不创建新的来源事实。

`L1ProjectionModelView` 不是摘要。它把完整 `NormalizedDocument.body` 按原顺序呈现，并在每个
Atom 与 Fragment 前后加确定性边界标记，例如：

```text
<SOURCE_ATOM id="source-atom:sha256:..." kind="list_item" lines="55-55">
  <SOURCE_FRAGMENT id="source-fragment:sha256:...">
  任务函数（LongTask除外）的CPU执行时长不能超过3分钟……
  </SOURCE_FRAGMENT>
</SOURCE_ATOM>
```

这样 DeepSeek 确实完整阅读了 L1，又只能返回已有 Fragment ID；不必同时再发送一份重复正文。
边界标记只存在于模型输入视图，不会写回 L1。DeepSeek 负责把 Fragment 组合成 Facet，不负责
决定 UTF-8 物理切片或生成新的规范文本。

### 9.2 第一版固定分类

建议第一版提供一组封闭的 `category_kind`：

```text
overview
applicability
api_and_symbols
component_behavior
constraint
prohibition
exception
numeric_limit
failure_behavior
lifecycle_and_resource
performance
security_and_permission
alternative_and_recommendation
example
diagnostic_and_observability
```

模型可以生成更通俗的 `display_title`，但不能创建未知 `category_kind`。这样可以避免不同文档
分别生成“限制”“注意”“警告”“不要这样做”等无法统一检索的分类名称。

`unclassified` 不属于语义分类，而是独立的兜底集合 `unclassified_fragment_ids`。一个 Fragment
只要作为 primary 进入任意 Facet，就不能同时进入这个兜底集合；仅作为 required context 不能
冒充已完成分类，否则覆盖率会失真。

### 9.3 多对多，而不是单选

同一 Fragment 可以同时进入多个 Facet，一个 Facet 也可以拥有多个分类。例如：

```text
“TaskPool 工作线程不支持使用 AppStorage”

→ prohibition
→ lifecycle_and_resource
→ component_behavior
→ subject: TaskPool
→ subject: AppStorage
```

这里重复的是“语义入口”，不是原文正文。第一版允许一个 Fragment 进入多个 Facet，也允许一个
Facet 拥有多个闭合分类，只拒绝完全相同的 canonical Facet。后续索引先以 `fragment_id` 去重，
回到完整上下文和 Evidence 时再按 `document_id + atom_id` 去重，不能把多标签算成多份独立证据。

### 9.4 映射建议合同

```jsonc
{
  "schema_version": "semantic-facet-set-draft-v1",
  "document_id": "...",
  "facets": [
    {
      "display_title": "TaskPool CPU 执行时间限制",
      "category_kinds": ["constraint", "numeric_limit"],
      "retrieval_aliases": ["三分钟限制", "TaskPool超时"],
      "context": {
        "primary_fragment_ids": ["source-fragment:sha256:..."],
        "required_context_fragment_ids": ["source-fragment:sha256:..."],
        "subject_terms": ["TaskPool", "LongTask"],
        "component_terms": ["TaskPool"],
        "role_terms": ["工作线程池"],
        "scenario_terms": ["普通任务执行"],
        "operation_terms": ["执行任务"],
        "condition_terms": ["LongTask除外"],
        "version_terms": []
      }
    }
  ],
  "unclassified_fragment_ids": []
}
```

`display_title`、Context terms 和 `retrieval_aliases` 都是 AI 生成的导航元数据，不是规范证据；
真正正文只能通过 Fragment -> Atom -> L1 回切。

### 9.5 不允许增量补丁作为最终真值

Grok 发现问题后，DeepSeek 必须输出一份完整的新 Facet Draft，而不是只返回“增加这一条、删除那
一条”的自由文本补丁。每次 Facet Set 都有新的 identity，并从完整输入重新校验。Facet identity
生成后，关系判断使用第二份完整 Relation Graph Draft 引用 sealed Facet ID，不能依赖模型自造的
临时名称。

---

## 10. 二级 Markdown 确定性编译

### 10.1 编译原则

编译器只允许做四类操作：

1. 生成明确标记为 derived metadata 的分类标题和来源注释；
2. 在每个分类下生成指向 Atom anchor 的链接；
3. 根据 Atom ID 从一级正文逐字复制一份按原始顺序排列的 Atom 原文库；
4. 生成 `unclassified` 和原始顺序兜底入口。

禁止：

- 调用模型补写正文；
- 修正文法、标点或 API 名称；
- 自动把多个 Atom 合成一句话；
- 删除模型认为不重要的内容；
- 因为一个 Atom 属于多个分类而重复复制其正文；
- 改写相对链接正文来伪装逐字一致。

相对链接的解析基准由 `SourceRef.relative_path` 提供；如需渲染到其他目录，应由阅读器处理 base
path，不修改 Source Atom 文本。

### 10.2 二级文档示例

```markdown
---
schema_version: retrieval-projection-markdown-v1
projection_id: document-projection:sha256:...
source_document_id: openharmony-docs:.../taskpool-introduction.md
source_revision: c8f5fb6c2fe03cf66b8a41c196ad7fc5e7891c47
use_scope: retrieval_projection_only_not_evidence
evidence_eligible: false
---

# TaskPool 检索视图

> 本文档是一级文档的派生检索视图。分类标题和别名由模型生成；原文与规范真值以一级文档为准。

## 检索目录

### API 与符号

- [setTransferList / setCloneList](#source-atom-a12)
  - 来源：TaskPool简介 / TaskPool注意事项，L31
  - 别名：转移列表、克隆列表、跨线程 ArrayBuffer

### 禁止事项

- [TaskPool 工作线程与 AppStorage](#source-atom-a27)
  - 来源：TaskPool简介 / TaskPool注意事项，L96
  - 别名：工作线程状态管理、TaskPool AppStorage

### 数值限制

- [CPU 执行时间](#source-atom-a18)
  - 来源：TaskPool简介 / TaskPool注意事项，L55
- [序列化数据大小](#source-atom-a21)
  - 来源：TaskPool简介 / TaskPool注意事项，L72

### 未分类

- [尚未可靠分类的原文单元](#source-atom-a30)

## 原文单元库

> 以下 Source Atom 按一级文档原始顺序排列。每段正文只出现一次；上面的多个分类只建立链接。

<a id="source-atom-a12"></a>
### Atom A12：setTransferList / setCloneList

<!-- atom_id: source-atom:sha256:... -->
<!-- source_lines: 31-31 -->
<!-- source_heading: TaskPool简介 / TaskPool注意事项 -->

ArrayBuffer参数在TaskPool中默认转移，需要设置转移列表的话可通过接口setTransferList()设置……

<a id="source-atom-a18"></a>
### Atom A18：原文位置 L55

任务函数（LongTask除外）的CPU执行时长不能超过3分钟……

<a id="source-atom-a21"></a>
### Atom A21：原文位置 L72

序列化传输的数据量限制为16MB。

<a id="source-atom-a27"></a>
### Atom A27：原文位置 L96

不支持在TaskPool工作线程中使用AppStorage。

<!-- 其余 Atom，包括 unclassified，仍按原始顺序完整出现 -->
```

这个结构同时保留两个能力：分类目录让检索从不同入口找到事实；原文单元库让内容完整存在且不会
因为重复分类污染 BM25、全文词频或向量聚合分数。

### 10.3 机器清单与人类 Markdown 同时存在

二级 Markdown 便于人和模型阅读，但运行时不能靠解析生成后的 Markdown 猜测 provenance。每份
二级文档必须同时保存 closed JSON manifest：

```text
projection.md                 人类/模型可读
projection-manifest.json      机器真值
mapping.json                  DeepSeek 分类结果
atom-set.json                 一级结构地址
```

运行时以 JSON Mapping/Manifest 建立“分类 → Atom → 原文”的连接，不能靠 Markdown 中的相邻文本
反推关系。检索结果按 `document_id + atom_id` 去重。

### 10.4 可选的展开调试视图

为了人工检查，可以额外生成 `projection-expanded.debug.md`，在每个分类下展开对应原文。但它必须：

- 明确标记为 debug-only；
- 不进入全文索引、向量索引或正式模型输入；
- 不参与 L2 identity；
- 仍由 Atom ID 确定性生成，不能让模型改写。

canonical L2 始终是“多分类链接 + 单份原文单元库”。

---

## 11. 机械校验

机械 verifier 不判断“分类好不好”，只判断链条是否真实、完整、可重建。

### 11.1 必须通过的硬门禁

1. `SourceRef`、raw content hash、normalized body hash 全部匹配。
2. `MarkdownDocumentMap` 可从同一 L1 重建。
3. `SourceAtomSet` 可从同一 L1 重建。
4. 所有 binding 只引用当前 AtomSet 中的 Atom。
5. 每个 eligible Atom 至少出现在一个 binding 或 `unclassified_atom_ids`。
6. `unclassified_atom_ids` 与普通 binding 都不得引用未知 Atom，且两者必须互斥。
7. canonical 二级 Markdown 的原文单元库恰好包含每个 eligible Atom 一次。
8. 每段来源正文与 L1 切片逐字一致；正文突变、截断或拼接均 hard fail。
9. 分类 binding 可以多对多，但相同 binding 不得重复，检索评分按 `atom_id` 去重。
10. 原始顺序兜底视图必须覆盖所有 Atom，包括 `unclassified_atom_ids` 中的 Atom。
11. Mapping、Projection 和 Manifest identity 都能重新计算。
12. Binding 等无序集合在 canonical sort 后顺序变化不能改变 identity；Atom 原始顺序必须与
    `ordinal` 一致。

### 11.2 不能由机械校验证明的内容

机械校验通过仍不能证明：

- 分类标题没有误导；
- Atom 放进了所有合理类别；
- `retrieval_aliases` 充分或正确；
- “上述”“该限制”等上下文已经足够；
- 二级文档能提高真实检索 Recall；
- Grok 或 DeepSeek 的语义判断正确。

这些由 Grok 审核和后续真实检索测试分别处理。

---

## 12. Grok 独立质量审核

### 12.1 Grok 的输入

每份审核 Packet 必须绑定：

```text
完整 L1ProjectionModelView（每个 Atom 正文恰好一次）
MarkdownDocumentMap
SourceAtomSet
DeepSeek Mapping
L2 分类索引
L2 Manifest
机械校验结果
DeepSeek Prompt/model/policy identities
Grok Review Prompt/model/policy identities
```

完整 canonical L2 可以由 Packet 中绑定的 L1、Mapping 和 Manifest 重建，因此 Prompt 不再重复发送
一份相同的 Atom 原文。机械 verifier 负责正文相等与重建；Grok 专注语义分类、标题、别名和上下文。

Grok 不接收 DeepSeek 的自由推理过程，也不读取历史“正确答案”，只对当前固定输入独立审核。

### 12.2 Grok 的审核维度

Grok 至少检查：

1. **分类完整性**：关键 API、限制、例外、禁止和失败行为是否有合理入口。
2. **多视图覆盖**：一个跨领域 Atom 是否只被放进一个过窄分类。
3. **标题忠实性**：标题有没有把“建议”升级成“必须”，或改变适用范围。
4. **别名忠实性**：检索别名是否能由引用 Atom 支持。
5. **上下文完整性**：代词、前置条件、说明块、表格和代码示例是否被拆坏。
6. **负向规则**：`不能`、`禁止`、`不支持`、`除外`、`否则`等是否被保留。
7. **数值规则**：版本号、时长、大小、数量、API level 是否被正确组织。
8. **未分类质量**：是否存在本可明确分类却被大量丢进 `unclassified_atom_ids` 的内容。
9. **检索可用性**：核心组件、API 和常见同义表达是否有入口。

### 12.3 Grok 输出合同

```jsonc
{
  "schema_version": "document-projection-review-receipt-v1",
  "receipt_id": "document-projection-review-receipt:sha256:...",
  "packet_id": "document-projection-review-packet:sha256:...",
  "reviewer_model": "pinned-grok-model-id",
  "decision": "accepted_for_shadow | needs_correction | abstain | invalid_output",
  "reviewed_atom_ids": ["完整覆盖当前 eligible Atom IDs"],
  "issues": [
    {
      "issue_id": "projection-review-issue:sha256:...",
      "severity": "critical | high | medium | low",
      "issue_type": "missing_category | misleading_title | context_break | bad_alias | other",
      "atom_ids": ["source-atom:sha256:..."],
      "source_quotes": ["一级 Packet 中的逐字短引用"],
      "expected_category_kinds": ["prohibition", "lifecycle_and_resource"],
      "explanation": "为什么当前映射不适合检索"
    }
  ],
  "qualification": "model_review_not_retrieval_truth"
}
```

Receipt 必须完整覆盖 Packet 的 eligible Atom ID 集合。这个 coverage 只证明 Grok 对外声明审核了
全部 Atom，不证明 Grok 一定没有遗漏语义。

### 12.4 Grok 不直接修文档

Grok 只产生 issue，不产生最终 Mapping。否则审核者会同时成为生成者，边界无法审计。

建议的修正循环：

```text
Grok needs_correction
        ↓
固定 issue list + 原完整输入
        ↓
DeepSeek 输出完整 Mapping v2
        ↓
重新编译 L2 v2
        ↓
机械校验
        ↓
Grok 重新审核
```

这个循环必须 append-only：每一轮都产生新的 Mapping Request、Dispatch Plan、Mapping、Projection、
Packet 和 Receipt identity，不能覆盖旧产物，也不能复用已经消费的 live Plan。每一次新的 DeepSeek
或 Grok 真实调用都需要对精确 Plan/Packet、正文摘要和预算重新获得用户批准，不允许把“修正循环”
解释成自动重试授权。

第一版策略建议最多允许两个修正版。超过上限仍有 critical/high 问题时，状态固定为
`blocked_for_curation`，不能为了“跑完”而自动接受。次数上限属于版本化 policy，不写死在通用
数据模型里。

### 12.5 Grok 不是最终 Truth

Grok 与 DeepSeek 都是模型，可能存在共同盲点。因此：

```text
accepted_for_shadow
≠ 人工批准
≠ 真实检索 Recall 合格
≠ 生产 Knowledge qualified
```

Grok 适合承担大规模机器审核；高风险样本仍需要少量人工抽查，真实效果必须由代码到文档 Truth
另行证明。

---

## 13. 状态机与发布边界

```text
draft_mapping
    DeepSeek 已输出 Mapping，尚未机械验证

structurally_valid
    Atom、Mapping 和 L2 可完整重建，正文零突变

model_reviewed
    已有有效 Grok Receipt，但不一定接受

accepted_for_shadow
    当前 Mapping 没有未解决 critical/high issue

retrieval_candidate
    可进入隔离的实验检索 Build

retrieval_qualified
    真实代码到文档评测达到冻结门禁
```

第一版 L2 Projection Build 即使达到 `accepted_for_shadow`，仍固定：

```text
use_scope = retrieval_projection_only_not_evidence
evidence_eligible = false
production_qualified = false
```

只有后续独立的 Retrieval Truth 和发布决策才能改变检索启用状态；本提案不设计把 L2 升级为
Clause Evidence 的快捷路径。

---

## 14. 二级文档如何用于从大到小检索

### 14.1 大范围候选不能由 Card 单独决定

第一层候选来源必须取并集：

```text
L2 文档目录和路径
∪ L2 分类标题
∪ L2 完整正文精确命中
∪ API/装饰器/组件符号命中
∪ 文档链接关系
∪ Tag/Dimension 的软扩展
∪ AI 代码语义扩展
```

Document Card 可以作为一项说明或排序信号，但不能成为“没有命中就淘汰文档”的硬门。

### 14.2 第一版不做原始正文固定向量分块

第一版建议：

- 不把 L1/L2 按固定 token 大小切成向量 Chunk；
- 对完整 L2 Markdown 建全文倒排/精确符号索引；
- 使用标题、目录、分类和链接扩大候选；
- 如需要向量，只给文档级 Card 或文档级检索简介建立向量，不返回孤立 Chunk；
- 命中后返回完整 `document_id`。

未来如确实需要局部向量，只能作为检索地址，必须绑定 parent document，并由真实消融证明增益；
不在第一版默认设计中。

### 14.3 大范围到小范围

```text
ReviewUnit / 组件 / API / AI 代码描述
        ↓
全库文档目录、全文和符号大范围召回
        ↓
保留几十篇候选也可以，第一版优先不漏
        ↓
DeepSeek 查看标题、完整目录、命中原因、L2 分类和 Card
        ↓
保留 strongly_relevant + possibly_relevant
        ↓
逐篇读取完整 L2，必要时同时读取完整 L1
        ↓
确定真正相关文档和原文章节
        ↓
所有最终引用 resolve 回 L1 SourceRef + SourceSpan
```

### 14.4 Tag 和 Dimension 的角色

Tag 和 Dimension 只能扩大或加权候选，不能做硬过滤：

```text
Tag 命中        → 相关文档家族加分
Tag 未命中      → 不得排除全文/API/目录已经命中的文档
AI Tag positive → 可扩展候选
AI Tag negative → 不得删除其他路径候选
```

这与当前代码分析 Tag 召回仍未证明的事实一致。

---

## 15. 与现有 Document Card、Clause 和 Retrieval 的关系

### 15.1 Document Card

现有 L1 Document Card 不删除、不改写历史产物。它继续证明现有结构链。

目标状态可以新增从 L2 生成的 Card，但必须使用新的 Prompt/identity，并明确：

```text
L1 Card ≠ L2 Card
Card ≠ L2 完整正文
Card ≠ Evidence
```

### 15.2 Clause 主链

L2 不替代：

```text
ClauseCandidate
→ Annotation
→ 两轮审核
→ Consensus
→ 人工 Curation
→ PublishedKnowledgeBuild
```

如果未来从 L2 发现 Clause，Clause 的 `SourceRef` 和 `SourceSpan` 必须指向 L1；L2 分类标题只能
作为 discovery provenance，不能成为规范正文。

### 15.3 Retrieval v1

当前 Retrieval v1 面向正式 KnowledgeIndex/Clause。L2 文档检索应先作为独立 shadow runtime，不能
把文档候选伪装成现有 `EvidencePack`。

第一版建议输出独立的：

```text
DocumentRetrievalCandidateSet
DocumentSelectionResult
```

只有当真实合同明确后，才讨论与 Clause Retrieval 的组合方式。

---

## 16. 失败处理与降级

| 失败 | 处理方式 |
|---|---|
| L1 Git identity 不匹配 | 停止，不生成 Atom |
| Markdown 无法完整结构化 | 使用 `raw_block` 和显式 diagnostic，不静默删除 |
| DeepSeek 不可用 | 保留 L1 和 Atom，L2 状态为未生成 |
| DeepSeek 输出未知 Atom | Mapping invalid，禁止编译 |
| Atom 未覆盖 | Mapping invalid；缺失 Atom 不能自动忽略 |
| 生成标题为空或重复 | closed schema/normalizer 拒绝或稳定去重，策略必须冻结 |
| L2 正文与 L1 不一致 | 机械 hard fail |
| Grok 不可用 | 保持 `structurally_valid`，不得标记 `accepted_for_shadow` |
| Grok abstain | 保留 Draft，不自动视为通过 |
| Grok critical/high issue | 进入修正循环；超限后 blocked |
| L1 revision 更新 | 旧 Atom、Mapping、L2、Receipt 和 Build 全部失效，重新构建 |
| 检索 runtime 不可用 | 可回退到 L1 目录/全文搜索，但不能声称 L2 质量通过 |

---

## 17. 安全、外发与审计

### 17.1 DeepSeek 外发

新的 Mapping 请求会包含完整一级 Markdown、Atom 目录和新 Prompt，因此需要独立于现有 Card
Campaign 的：

- 精确 source/revision/path allowlist；
- Prompt/model policy；
- wire body hash 和大小；
- 单文档与 Campaign 聚合预算；
- 用户对精确 Plan/Campaign 的批准；
- 一次性 marker 和无重试策略；
- 原始响应大小、API Key 回显和日志脱敏门禁。

### 17.2 Grok 外发

Grok Packet 会包含完整 L1 ModelView、Mapping、分类索引和校验摘要，但不会为比较目的再复制一份
L2 原文库。即便经过这种去重，它仍是新的模型输入，需要独立外发策略，不能复用 Clause Review
Packet 的授权。

Grok Receipt 必须绑定 exact Packet hash；模型身份、Prompt、响应和审核结论不能由调用方事后
自由替换。

### 17.3 审计身份

建议所有关键产物内容寻址：

```text
atom_set_id
mapping_request_id
mapping_plan_id
projection_id
mechanical_verification_id
grok_packet_id
grok_receipt_id
projection_build_id
```

self-hash 只证明内容 identity，不证明模型供应方、操作者身份或真实执行时间。部署级签名和正式
attestation 不属于第一版质量声明。

### 17.4 上下文、预算和调用粒度

第一版固定“一篇文档一次生成、一个版本一次审核”，不把多篇文档拼成一个 Prompt：

```text
1 个 L1 revision/path
→ 1 个 DeepSeek Mapping Plan
→ 1 个 Mapping version
→ 1 个 canonical L2
→ 1 个 Grok Review Packet
```

这样某篇文档失败不会污染其他文档，identity、成本和修订历史也能单独追踪。每个 Plan 在 dispatch
前必须冻结并展示：

- source/revision/path；
- Prompt hash、Schema hash、model policy；
- request body SHA-256 和请求字节数；
- 输入估算、最大输出 tokens、最大响应 bytes；
- 连接超时、总超时、最大尝试次数；
- API Key 与源码日志脱敏策略。

DeepSeek 只返回 Mapping JSON，不返回 L2 正文，因此输出预算主要随 binding 数量增长，不随原文
全文重复增长。Grok 也只返回 Receipt/issue JSON。如果某篇文档超过冻结的 full-document 输入上限，
第一版应返回 `document_too_large_for_full_projection` 并隔离，不能静默截断或偷偷改成分章调用。

Campaign 只是按固定顺序组织多份独立 Plan；任何一份失败都要保留 receipt，不进行隐式重试。

---

## 18. 测试与质量评估

### 18.1 单元与合同测试

必须覆盖：

- 段落、列表、嵌套列表、表格、代码块、引用块和 HTML 注释；
- 无标题 Markdown、重复标题、空章节和格式错误；
- CRLF/BOM 规范化后的 L1/L2 映射；
- Atom ID 稳定性和正文 hash；
- 未知 Atom、重复 binding、漏 Atom、错 document_id；
- 同一 Atom 多分类；
- `unclassified_atom_ids` 完整保留；
- “上述”“以下”“该限制”所需上下文关系；
- 相对链接不被改写；
- 同一 Atom 多分类时，canonical 正文仍只出现一次且评分按 Atom 去重；
- Grok receipt 的完整 Atom coverage 和 exact quote 校验；
- correction loop 身份与次数上限。

### 18.2 确定性 Golden

Golden 证明：

```text
同一 L1 + 同一 Atom Builder
→ 同一 AtomSet

同一 L1 + 同一 Mapping
→ 同一 L2 Markdown + Manifest
```

Golden 不证明 DeepSeek 分类正确或 Grok 判断正确。

### 18.3 真实二级文档 Pilot

建议继续使用当前已批准过来源登记、但需要重新批准新 Prompt 的 7 篇固定 Markdown，覆盖：

- 组件规格；
- 内存优化规格；
- Sendable 规则密集文档；
- TaskPool 长文档；
- Timer API；
- 生命周期；
- 状态管理。

TaskPool Pilot 至少应显式检查以下必须可检索事实：

```text
LongTask
3分钟
cpuDuration
ioDuration
setTransferList
setCloneList
16MB
IDLE
AppStorage
Worker替代条件
```

### 18.4 机械硬指标

这些指标可以在实现前冻结：

```text
physical_line_coverage = 100%
eligible_atom_mapping_coverage = 100%
source_text_mutation_count = 0
unknown_atom_reference_count = 0
duplicate_binding_count = 0
canonical_atom_body_occurrence_count = 1（逐 Atom）
scoring_duplicate_atom_count = 0
identity_rebuild_failure_count = 0
unresolved_grok_critical_issue_count = 0（进入实验 Build 前）
unresolved_grok_high_issue_count = 0（进入实验 Build 前）
```

同时必须报告但不在第一版盲目设合格线的诊断指标：

```text
unclassified_atom_rate
multi_category_atom_rate
context_expansion_rate
Grok issue rate by type/severity
DeepSeek → Grok correction count
```

### 18.5 真实检索指标

二级文档是否真正有效，必须使用：

```text
ReviewUnit / 代码查询
→ must-have documents
→ optional documents
→ irrelevant documents
→ forbidden documents
```

报告至少包括：

- Must-have Document Recall@1/@3/@5；
- must-have 文档完全漏失率；
- Precision@K；
- 无关文档数量；
- 禁止文档命中数；
- L1-only、Card-only、L2-only、L1+L2 的消融对比；
- 命中文档后最终答案的关键规则覆盖和原文引用正确性。

真实阈值应在 Pilot 和 Truth 样本冻结后确定，不能用 7/7 `valid_card` 或
`accepted_for_shadow` 代替。

---

## 19. 第一版最小范围

为了先证明结果，不把第一版做成大型平台，建议只包含：

1. 只支持 Registry 中固定 revision 的 `text/markdown`。
2. 只实现 Markdown 结构 Atom，不支持 PDF、HTML 和图片 OCR。
3. DeepSeek 只输出 closed JSON Mapping，不输出自由 Markdown。
4. 每个 Atom 可绑定多个分类；canonical 正文只保存一次；必须有 `unclassified_atom_ids` 兜底。
5. 程序确定性生成一份 L2 Markdown 和一份 Manifest。
6. 机械门禁要求 100% Atom coverage 和 0 source mutation。
7. Grok 单独审核，策略默认最多两个修正版；每次 live 仍需独立批准。
8. 只进入隔离的实验 L2 Build，固定非 Evidence、非生产资格。
9. 检索先使用目录、全文、符号和分类标题，不做原始 Markdown 固定向量分块。
10. 用固定 7 文档和少量真实 ReviewUnit 做第一轮端到端评测。

第一版明确不做：

- 在线自动学习分类 taxonomy；
- 任意模型动态切换；
- 数据库候选下推优化；
- 并行、缓存和调用成本优化；
- 自动把 L2 内容发布为 Clause；
- 未经 Truth 的生产启用。

---

## 20. 建议产物布局

以下只是目标布局，不是当前已存在文件：

```text
artifacts/document-projection/<projection-digest>/
├── 00_l1-source-manifest.json
├── 01_l1-source.md
├── 02_document-map.json
├── 03_source-atom-set.json
├── 04_deepseek-mapping-request.json
├── 05_deepseek-dispatch-plan.json
├── 06_deepseek-response.raw.json
├── 07_document-mapping.json
├── 08_l2-projection.md
├── 09_l2-projection-manifest.json
├── 10_mechanical-verification.json
├── 11_grok-review-packet.json
├── 12_grok-review-response.raw.json
├── 13_grok-review-receipt.json
└── 14_projection-build-manifest.json
```

如果发生修正循环，每一轮都使用独立的内容寻址子目录，不能覆盖旧 Mapping、L2 或 Receipt。

---

## 21. 主要风险与批判性判断

### 21.1 AI 分类仍可能错误

二级文档解决了“原文内容被摘要丢掉”的问题，但没有消灭模型分类错误。缓解方式是：

- Atom 100% 保留；
- 多对多分类；
- `unclassified_atom_ids`；
- Grok 独立审核；
- L1 全文搜索继续作为候选来源；
- 真实检索 Truth。

### 21.2 Grok 与 DeepSeek 可能共同犯错

使用不同模型能降低单模型偏差，但不能形成数学上的独立 Truth。Grok Receipt 必须固定标为机器
审核，不能在文档中写成“人工审核通过”。

### 21.3 重排会破坏上下文

逐字复制不等于语义一定完整。“上述限制”单独移动后仍可能无法理解。必须依靠
`required_context_atom_ids`、heading path、相邻关系和 Grok 审核保护。

### 21.4 多分类可能污染排名

一个 Atom 同时属于多个分类会增加索引边和别名数量。如果把同一正文复制多次，BM25 词频、全文
命中数或向量聚合可能被人为放大。因此 canonical L2 只保存一份 Atom 正文，分类只保存引用；候选
融合还必须按 `document_id + atom_id` 去重。分类边可以增加召回入口，不能伪装成多份独立证据。

### 21.5 固定分类也可能成为新瓶颈

分类过少会继续压缩语义，分类过多会导致不同文档不一致。第一版使用少量闭合的检查类型，加上
开放但非证据型的 subject/alias；任何 taxonomy 扩展必须版本化。

### 21.6 二级文档不能取代一级文档

如果未来检索只读 L2、不保留回到 L1 的路径，本方案就失去最重要的安全保证。因此所有候选、
引用和后续 Clause 必须能够解析回一级 `SourceRef + SourceSpan`。

---

## 22. 建议的实现分层

本节描述可独立验证的交付边界，不表示这些步骤已经开始实施。

### L2-01：Source Atom/Fragment/Facet 与机械编译基础

```text
NormalizedDocument + MarkdownDocumentMap
→ SourceAtomSet
→ SourceFragmentSet
→ caller-provided Facet/Relation fixture
→ explicit lossy Projection v1 adapter
→ caller-provided Mapping fixture
→ L2 Markdown + Manifest
→ strict verifier
```

当前已实现。只使用调用方 fixture，不调用模型；证明 Atom/Fragment 100% coverage、零改写、Facet
primary/unclassified 完整覆盖、Context/Relation 引用合法，以及新 sidecar 可以降级给旧 Projection
v1。它没有证明语义分类正确。

### L2-02：DeepSeek Facet/Relation 合同

```text
L1 + AtomSet + FragmentSet + frozen Prompt/policy
→ Request / Plan / inspection
→ fake response reducer
→ typed Facet Set
→ second-pass typed Relation Graph
```

Facet/Context/Relation 的本地数据合同已经实现；Prompt、Request/Plan、响应 adapter 和 runner 尚未
实现。默认不联网。

### L2-03：受控单文档 live

对一个固定 Markdown 执行一次精确批准的 DeepSeek Facet/Relation smoke，生成真实 L2，但仍未有 Grok
审核或质量资格。

### L2-04：Grok 审核合同

```text
L1 + AtomSet + FragmentSet + FacetSet + RelationGraph + L2 + mechanical result
→ Grok Packet
→ Receipt
→ correction decision
```

测试只使用 fake transport，真实调用需另行批准。

### L2-05：7 文档双模型 Campaign

对固定 7 文档执行 DeepSeek 生成、机械校验、Grok 审核和必要的有界修正，形成实验
`L2ProjectionBuild`。

### L2-06：真实代码到文档评测

使用 ReviewUnit 和 must-have document Truth 比较 L1-only、Card-only、L2-only 和组合检索，只有
这一层能够回答“二级文档是否真的减少漏找”。

---

## 23. 审核时需要确认的关键决策

下面给出本提案的明确默认选择，审核时可以逐项否决或调整：

| 决策 | 建议默认 | 原因 |
|---|---|---|
| L1 真值 | `NormalizedDocument.body`，同时保留 raw Git content hash | 与当前 Adapter 事实一致，既能逐字重建又不丢原始 blob identity |
| Atom 粒度 | 标题 section 内的段落、列表项、表格、代码块和 note/raw block | 比整章细，但不引入 token chunk 和模型拆句 |
| 分类 taxonomy | 先使用本文封闭列表，Pilot 后只通过新版本增删 | 防止每篇文档产生互不兼容的分类 |
| 多分类正文 | 分类边可多对多；canonical Atom 正文恰好一份 | 提高入口覆盖，同时避免排名被重复正文污染 |
| `unclassified` | 保留、展示并允许全文/精确检索命中 | 模型没分好类不等于原文应该消失 |
| required context | canonical 中保存引用；读取时确定性展开 | 不重复正文，同时避免“上述/以下”失去上下文 |
| 修正版上限 | policy 默认两个修正版，每次 live 单独批准 | 控制成本和死循环；不把循环当成自动重试授权 |
| Grok 通过后的范围 | 只进入 shadow Build | `accepted_for_shadow` 只是模型审核，不是检索 Truth |
| 原始 Markdown 向量切块 | 第一版不做固定 token chunk | 先验证文档级大范围召回，避免过早引入分块漏义 |
| 下游读取方式 | 先读目录和完整 L2 选文档，再解析到 L1 原文；正式证据仍走 Clause 主链 | L2 负责找文档，L1 负责来源真值，Clause 负责 Evidence 资格 |

---

## 24. 最终判断

这个架构的价值不在于“让 AI 写一篇更漂亮的文档”，而在于建立一条可以机械证明的转换链：

```text
原文事实不变
检索入口增加
同一事实可以多分类
模型不能删除来源正文
审核者不能直接篡改生成结果
所有命中都能回到一级文档
```

它比直接优化 Document Card 更接近当前漏检问题的根因，也比自由 AI 重写更可控。但它仍然只是一
个待实现方案：机械 100% coverage 可以由代码证明，DeepSeek/Grok 语义质量和真实文档 Recall 必须
通过后续真实 Campaign 与代码到文档 Truth 分别验证。
