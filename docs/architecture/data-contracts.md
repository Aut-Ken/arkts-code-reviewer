---
title: 跨模块数据契约
status: canonical
updated: 2026-07-15
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
| `KnowledgeClause/ApiSymbolCatalog` | Knowledge Build | Retrieval、Rules、Parser canonicalization |
| `ChangeSet` | Input | Parser、ReviewUnit、Output |
| `CodeFacts/FileAnalysis` | Parser | ReviewUnit、Feature Routing、Rules |
| `ReviewUnit/UnitFactScope` | ReviewUnit | Feature Routing、Context Planner、Evaluation |
| `UnitFeatureProfile/FeatureRoutingResult` | Feature Routing | Context Planner、Retrieval、Rules、Prompt、Evaluation |
| `EvidencePack` | Retrieval | Prompt、Finding Validator、Evaluation |
| `RuleFinding` | Rules | Prompt、Output、Evaluation |
| `ReviewRequest` | Prompt Builder | Final LLM |
| `Finding` | Final LLM + Validator | Output、Evaluation |
| `ReviewReport` | Output | GitCode、人工审核、Evaluation |

### 3.1 当前 SourceRecord

`/home/autken/Code/arkts-knowledge/registry/sources.yaml` 已登记 19 个本地仓库。当前 YAML
记录是已落盘事实，主项目尚未实现 loader：

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

目标 `SourceRegistryLoader` 将其校验为 Pydantic `SourceRecord`，并计算：

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

## 13. EvidencePack

```jsonc
{
  "schema_version": "evidence-pack-v1",
  "evidence_pack_id": "evidence-pack:sha256:...",
  "request_id": "retrieval-request:sha256:...",
  "retrieval_version": "retrieval-v1",
  "retrieval_config_fingerprint": "retrieval-config:sha256:...",
  "index_version": "knowledge-index:sha256:...",
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

`EvidencePack` 逐 Unit 划分 requested/covered/uncovered Dimensions，只包含 Baselined Clause，
并保存 match scope、适用性、exact/vector rank、RRF、authority、token 和结构化 diagnostic。
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
