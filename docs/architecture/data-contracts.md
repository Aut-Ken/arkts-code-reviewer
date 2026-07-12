---
title: 跨模块数据契约
status: canonical
updated: 2026-07-12
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
| `ReviewUnit` | ReviewUnit | Retrieval、Rules、Prompt |
| `Tags/Dimensions` | Feature Routing | Retrieval、Rules、Prompt、Evaluation |
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
    raw_prompt_use_allowed: false
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

## 4. 当前已实现入口

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

限制：

- 只保存新文件范围，不保存精确 added/deleted lines。
- 不保存 base 版本和旧文件内容。
- Git hunk 中的上下文行可能被误当成改动行。
- 删除-only、rename 和 binary file 没有正式契约。

## 5. 目标 ChangeSet

RU-4 不直接传递一个无法证明 revision 的字符串，而是使用三个最小对象。
这些是目标合同，当前 `FileInput/FileHunk` 尚未实现它们。

### 5.1 CodeSourceRef

| 字段 | 含义 |
|---|---|
| `source_ref_id` | 由 repository、revision、规范化 path 和 content hash 生成的稳定 ID |
| `repository` | 稳定仓库标识，不是本机绝对路径 |
| `revision` | 不可变 commit 或 snapshot ID；branch 不能用于复现 |
| `path` | 相对仓库根的规范化 POSIX 路径 |
| `content_hash` | 完整文件内容的 SHA-256 |

`CodeSourceRef` 只定位一个确定的源码快照。base/head 角色由引用它的
`ChangeAtom` 表达，不进入 source identity。

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

### 5.3 ChangeSet

| 字段 | 含义 |
|---|---|
| `change_set_id` | repository、base/head revision、规范化 files/atoms 和 normalizer version 的确定性 ID |
| `repository` | 变更所属的稳定仓库标识 |
| `base_revision` / `head_revision` | 本次分析的不可变端点 |
| `source_refs` | 本次变更引用的 `CodeSourceRef` 表，按 ID 稳定排序 |
| `files` | old/new path、`added \| modified \| deleted \| renamed` 和所属 `atom_ids` |
| `atoms` | 精确 `ChangeAtom` 表，按源角色、path、span 和 ID 稳定排序 |
| `diagnostics` | binary、无法归一化或源不可用等结构化诊断 |

纯 rename 可以没有 `ChangeAtom`；空文件仍有合法 `CodeSourceRef`；binary 变更不伪造行级
atom，而是进入 diagnostics。Git hunk context line 不得进入 `added_new_lines`。

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
- components/symbols 是文件级并集；Unit 内只能通过 span 内 declarations 投影。
- APIs/decorators/attributes/syntax/imports 都没有 Unit owner，只能作为 `file_hints`。
- `parser_layer=L1` 表示 sidecar 成功，不表示没有 ERROR/missing node；warnings 必须传播。
- Parser fact 不是 Finding evidence，缺少某个 fact 也不能证明源码中一定不存在该事实。

## 7. 目标 FileAnalysis

RU-3 的唯一正式路径是 Parser v2 先产生 occurrence-level facts，然后对同一
`CodeSourceRef` 只解析一次。`unit_exact + file_hints` 可以作为迁移/降级输出，但不能
替代下列目标合同或标记 RU-3 完成。

### 7.1 FileAnalysis

| 字段 | 含义 |
|---|---|
| `schema_version` / `analysis_id` | 结果 schema 版本，以及 source/parser 输入的确定性 ID |
| `source_ref` | 被解析的唯一 `CodeSourceRef` |
| `parser_version` | Parser 实现、sidecar 和 grammar 的可复现版本 |
| `parser_quality` | `layer`、`error_nodes`、`missing_nodes` 和结构化 `warnings` |
| `declarations` | 带 `declaration_id`、span 和 `parent_id` 的声明 occurrence |
| `review_regions` | 不是现有 declaration kind 的可评审区域 |
| `fact_occurrences` | 带 source span、owner 和 provenance 的事实 occurrence |
| `diagnostics` | 不可定位、owner 未解析或 Parser 降级等结构化诊断 |

### 7.2 FactOccurrence

| 字段 | 含义 |
|---|---|
| `occurrence_id` | source、kind、canonical name、span 和 owner 的确定性 ID |
| `kind` | 至少覆盖 component、api、decorator、attribute、symbol、syntax、string/resource、field read/write 和 import use |
| `name` / `canonical_name` | 源码名称与可选的归一化名称 |
| `span` | 1-based、end-inclusive 文件绝对行范围；列只是可选诊断信息 |
| `owner_ref` | tagged reference，精确指向 `declaration_id` 或 `region_id` |
| `provenance` | `L0 \| L1 \| recovered`，并保留产生器版本 |

### 7.3 ReviewRegion

| 字段 | 含义 |
|---|---|
| `region_id` | source、kind、symbol 和 span 的确定性 ID |
| `kind` | 第一版只有 `field_region \| import_region` |
| `symbol` | 字段或 import binding 的稳定展示名 |
| `span` | 完整语法区域的 1-based、end-inclusive 文件绝对行 |
| `owner_declaration_id` | field 所属 host；文件级 import 为 `null` |
| `provenance` | Parser layer 和产生器版本 |

owner 未解析的事实不得伪造 exact owner：可保留为 file hint 并带 diagnostic，但不能用于
Unit evidence。Parser v2 必须使用独立、人工审阅的 FileAnalysis Golden；不得改写 Parser v1
Golden expected/baseline。

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

目标调整：

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
```

当前 `changed_lines` 与 `file_changed_lines` 仍作为兼容字段保留，`unit_ref` 也仍可能在同名
UI occurrence 间重复；新的 `unit_id` 才是去重 source of truth。`full_text` 已按
`context_span` 从文件源码切片，`changed_new_lines` 使用 1-based 文件绝对行。

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
相同 ID。兼容字段仍在输出中，但旧的缺字段 `ReviewUnit(...)` 构造方式不属于兼容保证。

去重现在使用 `unit_id`，不再使用旧 `unit_ref`。同一 occurrence 的多个 hunk 合并；
同名但 span 不同的 occurrence 保持两个 Unit。第一阶段的 base/deleted source、关联上下文、
真正 code-context budget 和精确 Git diff 仍可明确标记 unsupported，不能伪造。

## 10. 目标 ReviewUnit

```jsonc
{
  "unit_id": "PhotoWall.ets@method:PhotoWall.loadImages:L14-L20",
  "file": "PhotoWall.ets",
  "source_ref_id": "code-source:sha256:...",
  "unit_kind": "method",
  "unit_symbol": "PhotoWall.loadImages",
  "source_span": {"start_line": 14, "end_line": 20},
  "context_span": {"start_line": 14, "end_line": 20},
  "change_atom_ids": ["change-atom:sha256:..."],
  "changed_new_lines": [17, 18],
  "source_text": "async loadImages() { ... }",
  "numbered_text": "14 | async loadImages() { ... }",
  "host_summary": {},
  "selection_reason": "innermost_changed_declaration",
  "context_degraded": false,
  "diagnostics": []
}
```

`source_ref_id` 在 RU-3 引入，`change_atom_ids` 在 RU-4 引入；当前 RU-1 兼容对象仍使用
`file/full_text/FileHunk`。`source_text` 必须严格等于 `context_span` 对应源码切片，
`numbered_text` 只能由它确定性派生。一个 change region 可以生成多个 ReviewUnit；一个
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

每个 file result 至少包含 `path`、可空 `source_ref_id`、稳定排序的 `units`、
`unassigned_hunk_lines`、`unassigned_change_atom_ids`、`parser_quality` 和 `diagnostics`。
RU-2 只填前者作为粗 FileHunk proxy；RU-3 增加 head `source_ref_id`，RU-4 再增加顶层
`change_set_id` 并填后者。diff 文件无 hunk、hunk 越界或
粗粒度 hunk 有未归属行时，必须通过这个信封显式表达，不能静默改为 full
review。

RU-2 的 schema version 是 `review-unit-build-v1`。其中 `parser_quality` 使用
`parser_layer` 和排序去重后的 `warnings`，只描述构建 Unit 所依据的完整文件 Parser 结果；
过渡期 Unit 二次 Parser 的质量进入 `AnalysisMetadata` 和对应 Unit diagnostics，不反向污染
完整文件质量。兼容字段 `AnalysisResult.review_units` 必须严格等于 `file_results[].units` 的
稳定扁平视图。

### 10.2 RU-5 上下文规划最小对象

RU-5 不是只取 Top-1 Unit。所有直接改动 owner 都是 Primary `ReviewUnit`；预算只能筛选
Supporting，不能删除 Primary 或 changed lines。字段级最小合同如下：

| 对象 | 最小字段 | 核心不变量 |
|---|---|---|
| `ContextCandidate` | `candidate_id`、`primary_unit_id`、`review_question_id`、`relation_type`、`target_source_ref_id`、`target_span`、`estimated_tokens`、`provenance_ref` | 只接受已解析 source/span 的有界候选；未解析目标不进候选表 |
| `SupportingSegment` | `segment_id`、`candidate_id`、`source_ref_id`、`source_span`、`source_text`、`question_binding`、`selection_reason`、`diagnostics` | `source_text` 严格等于 source/span 切片，且可追溯回一个 Primary 和问题 |
| `RelationEdge` | `edge_id`、`source_ref`、`target_ref`、`relation_type`、`quality`、`evidence_refs`、`provenance_ref` | 关系强度和 exact/degraded 质量是枚举，不是自由文本 |
| `ChangeGroup` | `group_id`、`primary_unit_ids`、`strong_edge_ids`、`diagnostics` | 仅 strong + exact 的 Primary-to-Primary edge 可建组；same-file/same-host 不单独构成强关系 |
| `ReviewContextBundle` | `bundle_id`、`group_id`、`primary_unit_ids`、`primary_question_bindings`、`supporting_segment_ids`、`relation_edge_ids`、`budget`、`dispatch_allowed`、`diagnostics` | 必须保留 group 内全部 Primary；Primary 单独超限时禁止调度，不静默截断 |
| `ContextPlanResult` | `context_plan_id`、`planner_version`、`change_set_id`、`primary_question_bindings`、`candidates`、`supporting_segments`、`relation_edges`、`change_groups`、`bundles`、`omitted_candidate_ids`、`budget_summary`、`diagnostics` | 这是 RU-5 唯一顶层产物；所有列表稳定排序，选中和舍弃都可追溯 |

RU-5 只能消费调用方显式传入的当前 source 或固定 revision 查询接口，不负责递归
扫描或构建全仓索引。

## 11. Unit Feature Context

目标实现不再对 ReviewUnit 二次 Parser，而是从 `FileAnalysis.facts` 按 span 和 owner 筛选：

```jsonc
{
  "unit_id": "...",
  "code_features": {
    "components": [],
    "apis": ["setInterval", "router.pushUrl"],
    "decorators": [],
    "attributes": [],
    "syntax": ["async_fn", "arrow_fn"],
    "tags": ["has_timer", "has_async", "has_navigation"]
  },
  "dimensions": ["DIM-05", "DIM-06", "DIM-07"],
  "intent_summary": "组件异步加载数据并创建周期定时器"
}
```

Dimensions 必须是 Unit 级。MR 级可以额外保存并集，用于预算和报告统计。

在 FactOccurrence 尚未实现时，禁止把文件级 set 复制为每个 Unit 的 exact facts。RU-3
唯一完成路线是先建立独立 Parser v2 Golden，获得带 span/owner 的 FactOccurrence 和
ReviewRegion，再删除二次 Parser。`unit_exact/file_hints` 仍是必要的输出作用域：前者只能由
occurrence/region 投影，后者只能扩大路由、永远不能作为 Unit evidence；但双作用域本身不能
替代 Parser v2 provenance，也不能单独通过 RU-3 门禁。

## 12. RetrievalQuery

```jsonc
{
  "request_id": "review-123",
  "index_alias": "current",
  "target_platform": {"release": "OpenHarmony-5.x", "api_level": 12},
  "token_budget": 8000,
  "units": [
    {
      "unit_id": "...",
      "code_features": {},
      "dimensions": [],
      "host_context": {},
      "intent_summary": "...",
      "parser_quality": {
        "layer": "L1",
        "context_degraded": false
      }
    }
  ]
}
```

## 13. EvidencePack

```jsonc
{
  "index_version": "idx-2026-07-10-001",
  "source_bundle_id": "sha256:...",
  "embedding_version": "candidate-model@internal-v1",
  "units": [
    {
      "unit_id": "...",
      "clauses": [
        {
          "rule_id": "RESOURCE/TIMER/R-01",
          "dimension_ids": ["DIM-05", "DIM-06"],
          "text": "组件创建的定时器应在不再使用时主动清理。",
          "status": "Baselined",
          "source_ref": {
            "source_id": "arkui-specs",
            "revision": "98bbe6578e0f...",
            "relative_path": "timer/Feat-01-spec.md",
            "anchor": "L40-L47",
            "authority": "feature_spec"
          },
          "matched_by": ["api:setInterval", "tag:has_timer"],
          "match_reason": "...",
          "score": 0.91,
          "rank_detail": {}
        }
      ],
      "uncovered_dimensions": []
    }
  ]
}
```

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
