---
title: 跨模块数据契约
status: canonical
updated: 2026-07-10
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

## 3. 数据所有权

| 数据 | 生产模块 | 主要消费模块 |
|---|---|---|
| `ChangeSet` | Input | Parser、ReviewUnit、Output |
| `CodeFacts/FileAnalysis` | Parser | ReviewUnit、Feature Routing、Rules |
| `ReviewUnit` | ReviewUnit | Retrieval、Rules、Prompt |
| `Tags/Dimensions` | Feature Routing | Retrieval、Rules、Prompt、Evaluation |
| `EvidencePack` | Retrieval | Prompt、Finding Validator、Evaluation |
| `RuleFinding` | Rules | Prompt、Output、Evaluation |
| `ReviewRequest` | Prompt Builder | Final LLM |
| `Finding` | Final LLM + Validator | Output、Evaluation |
| `ReviewReport` | Output | GitCode、人工审核、Evaluation |

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

限制：

- 只保存新文件范围，不保存精确 added/deleted lines。
- 不保存 base 版本和旧文件内容。
- Git hunk 中的上下文行可能被误当成改动行。
- 删除-only、rename 和 binary file 没有正式契约。

## 5. 目标 ChangeSet

```jsonc
{
  "repository": "team/project",
  "change_id": "mr-123@head-sha",
  "base_revision": "base-sha",
  "head_revision": "head-sha",
  "files": [
    {
      "old_path": "src/Old.ets",
      "new_path": "src/New.ets",
      "change_type": "modified",
      "old_content": "...",
      "new_content": "...",
      "regions": [
        {
          "old_span": {"start_line": 40, "end_line": 42},
          "new_span": {"start_line": 40, "end_line": 43},
          "added_lines": [41, 42],
          "deleted_old_lines": [41],
          "diff_positions": {"41": 18, "42": 19}
        }
      ]
    }
  ]
}
```

`change_type` 目标值：

```text
added | modified | deleted | renamed
```

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

集合字段没有 occurrence span，因此不能判断某个 API 或组件属于哪个 Unit。

## 7. 目标 FileAnalysis

目标是每个变化文件只解析一次，所有事实都带位置和宿主：

```jsonc
{
  "path": "src/pages/PhotoWall.ets",
  "content_hash": "sha256:...",
  "parser": {
    "layer": "L1",
    "version": "tree-sitter-arkts@0.2.0",
    "error_nodes": 0,
    "missing_nodes": 0,
    "warnings": []
  },
  "imports": [],
  "declarations": [],
  "facts": [
    {
      "kind": "api",
      "name": "setInterval",
      "canonical_name": "setInterval",
      "span": {
        "start_line": 17,
        "end_line": 17,
        "start_col": 20,
        "end_col": 31
      },
      "owner_ref": "PhotoWall.loadImages",
      "provenance": "L1"
    },
    {
      "kind": "component",
      "name": "Image",
      "canonical_name": "Image",
      "span": {
        "start_line": 26,
        "end_line": 28,
        "start_col": 11,
        "end_col": 50
      },
      "owner_ref": "PhotoWall.build",
      "provenance": "L1"
    }
  ]
}
```

`FactOccurrence.kind` 目标值至少覆盖：

```text
component | api | decorator | attribute | symbol | syntax | string_literal | resource_ref
```

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
```

## 10. 目标 ReviewUnit

```jsonc
{
  "unit_id": "PhotoWall.ets@method:PhotoWall.loadImages:L14-L20",
  "file": "PhotoWall.ets",
  "unit_kind": "method",
  "unit_symbol": "PhotoWall.loadImages",
  "source_span": {"start_line": 14, "end_line": 20},
  "context_span": {"start_line": 6, "end_line": 20},
  "changed_new_lines": [17, 18],
  "deleted_old_lines": [],
  "numbered_text": "14 | async loadImages() { ... }",
  "host_summary": {},
  "related_context": [],
  "selection_reason": "innermost changed method",
  "estimated_tokens": 420,
  "context_degraded": false,
  "diagnostics": []
}
```

约束：一个 change region 可以生成多个 ReviewUnit；一个 ReviewUnit 也可以合并多个 change region。

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

## 12. RetrievalQuery

```jsonc
{
  "request_id": "review-123",
  "index_alias": "current",
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
  "embedding_version": "bge-m3@internal-v1",
  "units": [
    {
      "unit_id": "...",
      "clauses": [
        {
          "rule_id": "RESOURCE/TIMER/R-01",
          "dimension_ids": ["DIM-05", "DIM-06"],
          "text": "组件创建的定时器应在不再使用时主动清理。",
          "status": "Baselined",
          "source_path": "timer/Feat-01-spec.md",
          "source_anchor": "L40-L47",
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

