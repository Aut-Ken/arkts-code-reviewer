from __future__ import annotations

from pathlib import Path

from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceRef
from arkts_code_reviewer.knowledge.parsing import parse_markdown_clauses
from arkts_code_reviewer.knowledge.parsing.golden_subject import (
    current_knowledge_subject,
)
from arkts_code_reviewer.knowledge_validation.golden import load_golden_suite

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/golden/knowledge/manifest.json"


def _document(body: str) -> NormalizedDocument:
    return NormalizedDocument(
        document_id="test:example.md",
        source_ref=SourceRef(
            source_id="test",
            revision="0" * 40,
            relative_path="example.md",
            anchor="document",
            authority="test",
            content_hash="0" * 64,
        ),
        media_type="text/markdown",
        title="测试",
        heading_tree=(),
        body=body,
        language="zh-CN",
        adapter_version="test-v1",
    )


def test_markdown_clause_parser_matches_all_structural_golden_cases() -> None:
    suite = load_golden_suite(MANIFEST)
    markdown_cases = [case for case in suite.cases if case.source.relative_path.endswith(".md")]

    assert len(markdown_cases) == 10
    for case in markdown_cases:
        actual = current_knowledge_subject(case)
        actual_clauses = [
            {key: value for key, value in clause.items() if key != "status"}
            for clause in actual["clauses"]
        ]
        expected_clauses = [
            {key: value for key, value in clause.items() if key != "status"}
            for clause in case.expected["clauses"]
        ]
        assert actual_clauses == expected_clauses, case.case_id
        assert actual["api_symbols"] == []


def test_background_prose_is_not_promoted_to_a_clause() -> None:
    suite = load_golden_suite(MANIFEST)
    case = next(case for case in suite.cases if case.case_id == "KG010")

    assert current_knowledge_subject(case)["clauses"] == []


def test_parser_never_promotes_unreviewed_candidates_to_baselined() -> None:
    suite = load_golden_suite(MANIFEST)
    cases = {case.case_id: case for case in suite.cases}

    assert current_knowledge_subject(cases["KG001"])["clauses"][0]["status"] == "Draft"
    assert (
        current_knowledge_subject(cases["KG009"])["clauses"][0]["status"]
        == "Deprecated"
    )


def test_note_and_code_example_remain_attached_to_their_clause() -> None:
    suite = load_golden_suite(MANIFEST)
    cases = {case.case_id: case for case in suite.cases}

    note = current_knowledge_subject(cases["KG005"])["clauses"][0]
    example = current_knowledge_subject(cases["KG006"])["clauses"][0]
    assert note["source_span"] == {"start_line": 3, "end_line": 5}
    assert example["source_span"] == {"start_line": 3, "end_line": 3}
    assert example["examples"][0]["source_span"] == {"start_line": 5, "end_line": 7}


def test_explicit_positive_and_negative_labels_own_the_following_fence() -> None:
    body = """# 约束

输入必须有效。

**正例：**

<!-- explanatory marker -->

```ts
validate(input);
```

**反例：**

```ts
skipValidation(input);
```

补充材料。

```ts
unownedExample();
```
"""
    result = parse_markdown_clauses(_document(body))

    assert len(result.clauses) == 1
    examples = result.clauses[0].candidate.examples
    assert [(item.kind, item.text) for item in examples] == [
        ("positive", "validate(input);"),
        ("negative", "skipValidation(input);"),
    ]
    assert "orphan_code_example:L21" in result.diagnostics


def test_body_api_version_becomes_applicability_without_guessing_multiple_versions() -> None:
    body = """# 版本约束

API version 12开始，调用方必须使用新接口。

API 9 与 API 12 的行为需要分别确认。
"""
    result = parse_markdown_clauses(_document(body))

    assert len(result.clauses) == 2
    assert result.clauses[0].candidate.applicability.min_api_level == 12
    assert result.clauses[1].candidate.applicability.min_api_level is None


def test_structured_rule_table_preserves_trigger_behavior_and_boundary() -> None:
    body = """# 导航规则

## 规则定义

| 规则ID | 类型 | 触发条件 | 预期行为 | 边界/约束 | 关联AC |
| --- | --- | --- | --- | --- | --- |
| R-1 | 约束 | 回调已注册 | 状态变化时触发回调 | 销毁后不得继续触发 | AC-1.1 |
"""
    result = parse_markdown_clauses(_document(body), rule_namespace="NAVIGATION")

    assert len(result.clauses) == 1
    clause = result.clauses[0]
    assert clause.rule_id == "NAVIGATION/R-1"
    assert clause.proposed_status == "Draft"
    assert clause.candidate.rule_type == "constraint"
    assert clause.candidate.text == (
        "触发条件：回调已注册；预期行为：状态变化时触发回调；"
        "边界/约束：销毁后不得继续触发"
    )
    assert clause.candidate.source_span.model_dump() == {
        "start_line": 7,
        "end_line": 7,
    }


def test_fallback_leaf_heading_collisions_are_order_independent() -> None:
    first = """# 根

## V1

### 规则

第一条必须保留。

## V2

### 规则

第二条必须保留。
"""
    second = """# 根

## V2

### 规则

第二条必须保留。

## V1

### 规则

第一条必须保留。
"""

    first_result = parse_markdown_clauses(_document(first))
    second_result = parse_markdown_clauses(_document(second))
    first_ids = {item.candidate.text: item.rule_id for item in first_result.clauses}
    second_ids = {item.candidate.text: item.rule_id for item in second_result.clauses}

    assert first_ids == second_ids
    assert all("@heading-" in rule_id for rule_id in first_ids.values())
    assert len(first_ids) == len(set(first_ids.values())) == 2
    assert any(
        item.startswith("ambiguous_heading_anchor:")
        for item in first_result.diagnostics
    )


def test_neighbors_do_not_cross_heading_boundaries() -> None:
    body = """# 根

## 同组

第一条必须执行。

第二条不得省略。

## 另一组

第三条必须执行。
"""
    result = parse_markdown_clauses(_document(body))
    by_text = {item.candidate.text: item.candidate for item in result.clauses}

    first = by_text["第一条必须执行。"]
    second = by_text["第二条不得省略。"]
    third = by_text["第三条必须执行。"]
    assert first.neighbor_candidate_ids == (second.candidate_id,)
    assert second.neighbor_candidate_ids == (first.candidate_id,)
    assert third.neighbor_candidate_ids == ()


def test_mult_paragraph_blockquote_stays_attached_to_one_clause() -> None:
    body = """# 生命周期

订阅必须解除。

> 注意：第一段说明。
>
> 第二段不得忽略。
"""
    result = parse_markdown_clauses(_document(body))

    assert len(result.clauses) == 1
    candidate = result.clauses[0].candidate
    assert candidate.text == "订阅必须解除。第一段说明。第二段不得忽略。"
    assert candidate.source_span.end_line == 7


def test_nested_normative_list_items_are_not_silently_skipped() -> None:
    body = """# 配置约束

- 配置规则如下：
  - 默认不应支持未知装饰器。
  - 配置不得为空数组。
"""
    result = parse_markdown_clauses(_document(body))

    assert [item.candidate.text for item in result.clauses] == [
        "默认不应支持未知装饰器。",
        "配置不得为空数组。",
    ]
    assert all(
        item.candidate.parent_context == "配置规则如下：" for item in result.clauses
    )


def test_api_metadata_labels_and_unowned_examples_are_not_promoted() -> None:
    body = """# 定时器 API

**返回值：**

返回任务编号。

**示例1：**

```ts
setTimeout(run, 1000);
```
"""
    result = parse_markdown_clauses(_document(body))

    assert result.clauses == ()
    assert "orphan_example_label:L7" in result.diagnostics
    assert "orphan_code_example:L9" in result.diagnostics


def test_list_behavior_rule_owns_its_negative_log_and_positive_examples() -> None:
    body = """# 使用限制

1. ArkTS会进行校验并产生告警日志。

   【反例】

   ```ts
   invalid();
   ```

   编译告警日志如下：

   ```text
   warning
   ```

   【正例】

   ```ts
   valid();
   ```
"""
    result = parse_markdown_clauses(_document(body))

    assert len(result.clauses) == 1
    clause = result.clauses[0].candidate
    assert clause.rule_type == "behavior"
    assert [(item.kind, item.text) for item in clause.examples] == [
        ("negative", "invalid();"),
        ("negative", "warning"),
        ("positive", "valid();"),
    ]


def test_complete_normative_heading_becomes_a_draft_clause() -> None:
    body = """# Sendable 约束

## Sendable类必须继承自Sendable类

该限制用于保持对象可共享。
"""
    result = parse_markdown_clauses(_document(body))

    assert len(result.clauses) == 1
    clause = result.clauses[0]
    assert clause.proposed_status == "Draft"
    assert clause.candidate.text == "Sendable类必须继承自Sendable类"
    assert clause.candidate.source_span.model_dump() == {
        "start_line": 3,
        "end_line": 3,
    }
