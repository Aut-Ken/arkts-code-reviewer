from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from arkts_code_reviewer.code_analysis.analyzer import CodeAnalyzer
from arkts_code_reviewer.code_analysis.change_set import (
    ChangeAtomInput,
    ChangedFileInput,
    ChangeSet,
    CodeSourceSnapshot,
    normalize_change_set,
)
from arkts_code_reviewer.code_analysis.context_planning import (
    ContextCandidate,
    QuestionBinding,
    RelationEdge,
    estimate_code_tokens,
    source_span_ref_id,
)
from arkts_code_reviewer.code_analysis.file_analysis_models import (
    CodeSourceRef,
    DeclarationOccurrence,
    ExactRange,
    FactOccurrence,
    FileAnalysis,
    FileParseResult,
    FileParserQuality,
    OwnerRef,
    ScopedFacts,
)
from arkts_code_reviewer.code_analysis.file_analysis_parser import (
    LegacyFileAnalysisAdapter,
)
from arkts_code_reviewer.code_analysis.lexical import LexicalParser
from arkts_code_reviewer.code_analysis.models import (
    CodeFacts,
    ReviewUnitSpan,
    SourceSpan,
)


class CountingFileParser:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.delegate = LegacyFileAnalysisAdapter(LexicalParser())

    def parse_file(
        self,
        source_ref: CodeSourceRef,
        source: str,
    ) -> FileParseResult:
        self.calls.append(source_ref.source_ref_id)
        return self.delegate.parse_file(source_ref, source)


class FixtureFileParser:
    def __init__(self, results: dict[str, FileParseResult]) -> None:
        self.results = results

    def parse_file(
        self,
        source_ref: CodeSourceRef,
        source: str,
    ) -> FileParseResult:
        source_ref.verify_content(source)
        return self.results[source_ref.source_ref_id]


def _snapshot(path: str, content: str, revision: str) -> CodeSourceSnapshot:
    content_hash = f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"
    return CodeSourceSnapshot(
        source_ref=CodeSourceRef.create(
            repository="repo",
            revision=revision,
            path=path,
            content_hash=content_hash,
        ),
        content=content,
    )


def _replacement_change(
    base: CodeSourceSnapshot,
    head: CodeSourceSnapshot,
    *,
    atoms: tuple[ChangeAtomInput, ...],
) -> ChangeSet:
    return normalize_change_set(
        repository="repo",
        base_revision="base",
        head_revision="head",
        files=(
            ChangedFileInput(
                status="modified",
                old_path=base.source_ref.path,
                new_path=head.source_ref.path,
                old_snapshot=base,
                new_snapshot=head,
                atoms=atoms,
            ),
        ),
    )


def _scoped_parse_result(
    snapshot: CodeSourceSnapshot,
    *,
    exact_api: str,
    file_hint_api: str,
) -> FileParseResult:
    source_ref_id = snapshot.source_ref.source_ref_id
    total_utf16 = len(snapshot.content.encode("utf-16-le")) // 2
    declaration = DeclarationOccurrence.create(
        source_ref_id=source_ref_id,
        kind="function",
        name="changed",
        qualified_name="changed",
        span=SourceSpan(1, 3),
        exact_range=ExactRange(1, 3, 0, total_utf16),
    )
    line_one_utf16 = len(snapshot.content.splitlines(keepends=True)[0].encode("utf-16-le")) // 2
    api_start = line_one_utf16 + 2
    api_end = api_start + len(exact_api.encode("utf-16-le")) // 2
    occurrence = FactOccurrence.create(
        source_ref_id=source_ref_id,
        kind="api",
        name=exact_api,
        canonical_name=exact_api,
        span=SourceSpan(2, 2),
        exact_range=ExactRange(2, 2, api_start, api_end),
        owner_ref=OwnerRef("declaration", declaration.declaration_id),
    )
    facts = CodeFacts(
        path=snapshot.source_ref.path,
        apis={exact_api, file_hint_api},
        parser_layer="L1",
    )
    analysis = FileAnalysis.create(
        source_ref=snapshot.source_ref,
        parser_version="fixture-file-analysis-v1",
        parser_quality=FileParserQuality(
            layer="L1",
            error_nodes=0,
            missing_nodes=0,
        ),
        file_hints=ScopedFacts.from_code_facts(facts),
        declarations=(declaration,),
        fact_occurrences=(occurrence,),
    )
    return FileParseResult(analysis=analysis, compatibility_facts=facts)


def _two_declaration_parse_result(
    snapshot: CodeSourceSnapshot,
) -> tuple[FileParseResult, DeclarationOccurrence, DeclarationOccurrence]:
    lines = snapshot.content.splitlines(keepends=True)
    if len(lines) != 2:
        raise ValueError("fixture requires exactly two lines")
    first_end = len(lines[0].encode("utf-16-le")) // 2
    total_end = first_end + len(lines[1].encode("utf-16-le")) // 2
    first = DeclarationOccurrence.create(
        source_ref_id=snapshot.source_ref.source_ref_id,
        kind="function",
        name="changed",
        qualified_name="changed",
        span=SourceSpan(1, 1),
        exact_range=ExactRange(1, 1, 0, first_end),
    )
    second = DeclarationOccurrence.create(
        source_ref_id=snapshot.source_ref.source_ref_id,
        kind="function",
        name="helper",
        qualified_name="helper",
        span=SourceSpan(2, 2),
        exact_range=ExactRange(2, 2, first_end, total_end),
    )
    facts = CodeFacts(path=snapshot.source_ref.path, parser_layer="L1")
    analysis = FileAnalysis.create(
        source_ref=snapshot.source_ref,
        parser_version="fixture-file-analysis-v1",
        parser_quality=FileParserQuality(
            layer="L1",
            error_nodes=0,
            missing_nodes=0,
        ),
        file_hints=ScopedFacts(),
        declarations=(first, second),
    )
    return FileParseResult(analysis=analysis, compatibility_facts=facts), first, second


def test_analyze_change_set_parses_each_base_head_source_once() -> None:
    base = _snapshot(
        "src/A.ets",
        "struct A {\n  foo() {\n    return 1\n  }\n}\n",
        "base",
    )
    head = _snapshot(
        "src/A.ets",
        "struct A {\n  foo() {\n    return 2\n  }\n}\n",
        "head",
    )
    change_set = _replacement_change(
        base,
        head,
        atoms=(
            ChangeAtomInput(
                kind="replacement",
                old_span=ReviewUnitSpan(3, 3),
                new_span=ReviewUnitSpan(3, 3),
                added_new_lines=(3,),
                deleted_old_lines=(3,),
            ),
        ),
    )
    parser = CountingFileParser()

    result = CodeAnalyzer(file_parser=parser).analyze_change_set(
        change_set,
        {
            head.source_ref.source_ref_id: head,
            base.source_ref.source_ref_id: base,
        },
    )

    assert len(parser.calls) == 2
    assert len(set(parser.calls)) == 2
    assert result.change_set == change_set
    assert result.review_unit_build_result is not None
    assert result.review_unit_build_result.schema_version == "review-unit-build-v3"
    assert [unit.source_role for unit in result.review_units] == ["base", "head"]
    assert len({unit.unit_id for unit in result.review_units}) == 2
    assert result.review_units[0].changed_old_lines == [3]
    assert result.review_units[0].changed_new_lines == []
    assert result.review_units[1].changed_old_lines == []
    assert result.review_units[1].changed_new_lines == [3]
    assert "return 1" in result.review_units[0].full_text
    assert "return 2" in result.review_units[1].full_text


def test_multiple_atoms_and_units_do_not_increase_parser_calls() -> None:
    base = _snapshot(
        "src/A.ets",
        "struct A {\n  first() { return 1 }\n  second() { return 2 }\n}\n",
        "base",
    )
    head = _snapshot(
        "src/A.ets",
        "struct A {\n  first() { return 3 }\n  second() { return 4 }\n}\n",
        "head",
    )
    change_set = _replacement_change(
        base,
        head,
        atoms=(
            ChangeAtomInput(
                kind="replacement",
                old_span=ReviewUnitSpan(2, 2),
                new_span=ReviewUnitSpan(2, 2),
                added_new_lines=(2,),
                deleted_old_lines=(2,),
            ),
            ChangeAtomInput(
                kind="replacement",
                old_span=ReviewUnitSpan(3, 3),
                new_span=ReviewUnitSpan(3, 3),
                added_new_lines=(3,),
                deleted_old_lines=(3,),
            ),
        ),
    )
    parser = CountingFileParser()

    result = CodeAnalyzer(file_parser=parser).analyze_change_set(
        change_set,
        {
            base.source_ref.source_ref_id: base,
            head.source_ref.source_ref_id: head,
        },
    )

    assert len(parser.calls) == 2
    assert len(result.review_units) >= 2


def test_snapshot_identity_mismatch_fails_before_any_parser_call() -> None:
    head = _snapshot("src/A.ets", "const value = 1\n", "head")
    change_set = normalize_change_set(
        repository="repo",
        base_revision="base",
        head_revision="head",
        files=(
            ChangedFileInput(
                status="added",
                old_path=None,
                new_path="src/A.ets",
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="addition",
                        old_span=None,
                        new_span=ReviewUnitSpan(1, 1),
                        added_new_lines=(1,),
                    ),
                ),
            ),
        ),
    )
    wrong = _snapshot("src/B.ets", "const value = 1\n", "head")
    parser = CountingFileParser()

    with pytest.raises(ValueError, match="source_ref must match"):
        CodeAnalyzer(file_parser=parser).analyze_change_set(
            change_set,
            {head.source_ref.source_ref_id: wrong},
        )

    assert parser.calls == []


def test_pure_rename_parses_both_sources_without_fabricating_units() -> None:
    base = _snapshot("src/A.ets", "struct A {}\n", "base")
    head = _snapshot("src/B.ets", "struct A {}\n", "head")
    change_set = normalize_change_set(
        repository="repo",
        base_revision="base",
        head_revision="head",
        files=(
            ChangedFileInput(
                status="renamed",
                old_path="src/A.ets",
                new_path="src/B.ets",
                old_snapshot=base,
                new_snapshot=head,
            ),
        ),
    )
    parser = CountingFileParser()

    result = CodeAnalyzer(file_parser=parser).analyze_change_set(
        change_set,
        {
            base.source_ref.source_ref_id: base,
            head.source_ref.source_ref_id: head,
        },
    )

    assert len(parser.calls) == 2
    assert result.review_units == []
    assert result.review_unit_build_result is not None
    assert len(result.review_unit_build_result.file_results) == 2


def test_binary_change_propagates_diagnostic_without_parser_or_fake_unit() -> None:
    change_set = normalize_change_set(
        repository="repo",
        base_revision="base",
        head_revision="head",
        files=(
            ChangedFileInput(
                status="modified",
                old_path="assets/a.bin",
                new_path="assets/a.bin",
                is_binary=True,
            ),
        ),
    )
    parser = CountingFileParser()

    result = CodeAnalyzer(file_parser=parser).analyze_change_set(change_set, {})

    assert parser.calls == []
    assert result.review_units == []
    assert result.change_set is not None
    assert result.change_set.diagnostics[0].code == "binary_source_unavailable"
    assert result.review_unit_build_result is not None
    assert [
        diagnostic.code
        for diagnostic in result.review_unit_build_result.diagnostics
    ] == ["binary_change_unsupported"]
    assert result.to_dict()["change_set"] == change_set.to_dict()


def test_change_set_analysis_keeps_unit_exact_separate_from_file_hints() -> None:
    base = _snapshot(
        "src/Scoped.ets",
        "function changed() {\n  router.pushUrl()\n}\n",
        "base",
    )
    head = _snapshot(
        "src/Scoped.ets",
        "function changed() {\n  setInterval()\n}\n",
        "head",
    )
    change_set = _replacement_change(
        base,
        head,
        atoms=(
            ChangeAtomInput(
                kind="replacement",
                old_span=ReviewUnitSpan(2, 2),
                new_span=ReviewUnitSpan(2, 2),
                added_new_lines=(2,),
                deleted_old_lines=(2,),
            ),
        ),
    )
    parse_results = {
        base.source_ref.source_ref_id: _scoped_parse_result(
            base,
            exact_api="router.pushUrl",
            file_hint_api="http.request",
        ),
        head.source_ref.source_ref_id: _scoped_parse_result(
            head,
            exact_api="setInterval",
            file_hint_api="http.request",
        ),
    }

    analyzer = CodeAnalyzer(file_parser=FixtureFileParser(parse_results))
    snapshots = {
        base.source_ref.source_ref_id: base,
        head.source_ref.source_ref_id: head,
    }
    result = analyzer.analyze_change_set(change_set, snapshots)

    base_retrieval, head_retrieval = result.retrieval_query.units
    assert base_retrieval.code_features.apis == ["router.pushUrl"]
    assert head_retrieval.code_features.apis == ["setInterval"]
    assert "has_network" not in base_retrieval.code_features.tags
    assert "has_network" not in head_retrieval.code_features.tags
    assert "has_network" in base_retrieval.routing_tags
    assert "has_network" in head_retrieval.routing_tags
    assert result.feature_routing_result is not None
    profiles = {
        profile.unit_id: profile for profile in result.feature_routing_result.units
    }
    assert profiles[base_retrieval.unit_id or ""].exact_tags == ("has_navigation",)
    assert profiles[head_retrieval.unit_id or ""].exact_tags == ("has_timer",)
    assert profiles[base_retrieval.unit_id or ""].routing_tags == (
        "has_navigation",
        "has_network",
    )
    assert "RQ-network" not in profiles[base_retrieval.unit_id or ""].review_question_ids
    assert "RQ-resource" in profiles[head_retrieval.unit_id or ""].review_question_ids
    assert result.unit_fact_scopes[0].unit_exact.apis == ("router.pushUrl",)
    assert result.unit_fact_scopes[1].unit_exact.apis == ("setInterval",)
    assert result.unit_fact_scopes[0].file_hints.apis == (
        "http.request",
        "router.pushUrl",
    )
    assert result.unit_fact_scopes[1].file_hints.apis == (
        "http.request",
        "setInterval",
    )
    assert [unit.owner_ref for unit in result.review_units] == [
        OwnerRef(
            "declaration",
            parse_results[base.source_ref.source_ref_id]
            .analysis.declarations[0]
            .declaration_id,
        ),
        OwnerRef(
            "declaration",
            parse_results[head.source_ref.source_ref_id]
            .analysis.declarations[0]
            .declaration_id,
        ),
    ]
    assert result.to_dict()["feature_routing_result"] == (
        result.feature_routing_result.to_dict()
    )
    assert result.to_dict()["schema_version"] == "analysis-result-v1"

    plan = analyzer.plan_context(
        result,
        source_snapshots=snapshots,
        code_context_budget=10_000,
    )
    assert plan.primary_question_bindings == tuple(
        QuestionBinding(
            binding.primary_unit_id,
            binding.review_question_id,
        )
        for binding in result.feature_routing_result.question_bindings
    )
    assert {
        binding.review_question_id for binding in plan.primary_question_bindings
    } == {"RQ-correctness", "RQ-navigation", "RQ-resource"}
    assert "RQ-network" not in {
        binding.review_question_id for binding in plan.primary_question_bindings
    }

    with pytest.raises(ValueError, match="unregistered Tag"):
        replace(
            base_retrieval.code_features,
            tags=["fabricated_tag"],
        )
    with pytest.raises(ValueError, match="unregistered IDs"):
        replace(base_retrieval, dimensions=["DIM-99"])
    with pytest.raises(ValueError, match="integer >= 1"):
        replace(result.retrieval_query.mr_context, token_budget=-1)
    with pytest.raises(ValueError, match="unregistered Dimension"):
        replace(
            result.retrieval_query.mr_context,
            triggered_dimensions=["DIM-99"],
        )
    forged_retrieval = replace(
        base_retrieval,
        code_features=replace(
            base_retrieval.code_features,
            tags=["has_image"],
        ),
    )
    forged_query = replace(
        result.retrieval_query,
        units=[forged_retrieval, head_retrieval],
    )
    with pytest.raises(ValueError, match="compatibility view"):
        replace(result, retrieval_query=forged_query)
    with pytest.raises(ValueError, match="must use FeatureRoutingResult"):
        replace(result, feature_routing_result=None)
    with pytest.raises(ValueError, match="schema_version"):
        replace(result, schema_version="analysis-result-legacy")
    detached_retrieval = replace(base_retrieval, unit_fact_scope=None)
    detached_query = replace(
        result.retrieval_query,
        units=[detached_retrieval, head_retrieval],
    )
    with pytest.raises(ValueError, match="retain their UnitFactScope"):
        replace(result, retrieval_query=detached_query)
    renamed_retrieval = replace(base_retrieval, unit_ref="forged@unit")
    renamed_query = replace(
        result.retrieval_query,
        units=[renamed_retrieval, head_retrieval],
    )
    with pytest.raises(ValueError, match="align by unit_ref"):
        replace(result, retrieval_query=renamed_query)


def test_plan_context_uses_every_unit_from_complete_change_analysis() -> None:
    base = _snapshot(
        "src/A.ets",
        "struct A {\n  first() { return 1 }\n  second() { return 2 }\n}\n",
        "base",
    )
    head = _snapshot(
        "src/A.ets",
        "struct A {\n  first() { return 3 }\n  second() { return 4 }\n}\n",
        "head",
    )
    change_set = _replacement_change(
        base,
        head,
        atoms=(
            ChangeAtomInput(
                kind="replacement",
                old_span=ReviewUnitSpan(2, 2),
                new_span=ReviewUnitSpan(2, 2),
                added_new_lines=(2,),
                deleted_old_lines=(2,),
            ),
            ChangeAtomInput(
                kind="replacement",
                old_span=ReviewUnitSpan(3, 3),
                new_span=ReviewUnitSpan(3, 3),
                added_new_lines=(3,),
                deleted_old_lines=(3,),
            ),
        ),
    )
    analyzer = CodeAnalyzer(file_parser=CountingFileParser())
    snapshots = {
        base.source_ref.source_ref_id: base,
        head.source_ref.source_ref_id: head,
    }
    analysis = analyzer.analyze_change_set(change_set, snapshots)

    correspondence_units = analysis.review_units
    forged_correspondence = RelationEdge.create(
        source_ref=correspondence_units[0].unit_id,
        target_ref=correspondence_units[1].unit_id,
        relation_type="change_correspondence",
        strength="strong",
        quality="exact",
        evidence_refs=(f"change-atom:sha256:{'f' * 64}",),
        provenance_ref=change_set.change_set_id,
    )
    with pytest.raises(ValueError, match="planner-derived only"):
        analyzer.plan_context(
            analysis,
            primary_question_bindings=tuple(
                QuestionBinding(unit.unit_id, "RQ-correctness")
                for unit in analysis.review_units
            ),
            source_snapshots=snapshots,
            relation_edges=(forged_correspondence,),
            code_context_budget=1000,
        )

    with pytest.raises(ValueError, match="must match FeatureRoutingResult"):
        analyzer.plan_context(
            analysis,
            primary_question_bindings=tuple(
                QuestionBinding(unit.unit_id, "correctness")
                for unit in analysis.review_units
            ),
            source_snapshots=snapshots,
            code_context_budget=1000,
        )

    plan = analyzer.plan_context(
        analysis,
        source_snapshots=snapshots,
        code_context_budget=1000,
    )

    assert {
        unit_id
        for group in plan.change_groups
        for unit_id in group.primary_unit_ids
    } == {unit.unit_id for unit in analysis.review_units}
    assert {
        unit_id
        for bundle in plan.bundles
        for unit_id in bundle.primary_unit_ids
    } == {unit.unit_id for unit in analysis.review_units}
    assert plan.primary_question_bindings == tuple(
        QuestionBinding(unit.unit_id, "RQ-correctness")
        for unit in sorted(analysis.review_units, key=lambda item: item.unit_id)
    )


def test_plan_context_accepts_only_exact_parser_occurrence_boundaries() -> None:
    base = _snapshot(
        "src/A.ets",
        "function changed() { return 0 }\nfunction helper() { return 2 }\n",
        "base",
    )
    head = _snapshot(
        "src/A.ets",
        "function changed() { return 1 }\nfunction helper() { return 2 }\n",
        "head",
    )
    support = _snapshot(
        "src/Support.ets",
        "function changed() { return 9 }\nfunction helper() { return 2 }\n",
        "support-revision",
    )
    base_parse_result, _, _ = _two_declaration_parse_result(base)
    head_parse_result, _, _ = _two_declaration_parse_result(head)
    support_parse_result, helper, _ = _two_declaration_parse_result(support)
    change_set = _replacement_change(
        base,
        head,
        atoms=(
            ChangeAtomInput(
                kind="replacement",
                old_span=ReviewUnitSpan(1, 1),
                new_span=ReviewUnitSpan(1, 1),
                added_new_lines=(1,),
                deleted_old_lines=(1,),
            ),
        ),
    )
    analyzer = CodeAnalyzer(
        file_parser=FixtureFileParser(
            {
                base.source_ref.source_ref_id: base_parse_result,
                head.source_ref.source_ref_id: head_parse_result,
            }
        )
    )
    change_snapshots = {
        base.source_ref.source_ref_id: base,
        head.source_ref.source_ref_id: head,
    }
    analysis = analyzer.analyze_change_set(
        change_set,
        change_snapshots,
    )
    context_snapshots = {
        **change_snapshots,
        support.source_ref.source_ref_id: support,
    }
    primary = next(
        unit for unit in analysis.review_units if unit.source_role == "head"
    )
    edge = RelationEdge.create(
        source_ref=primary.unit_id,
        target_ref=source_span_ref_id(
            support.source_ref.source_ref_id,
            helper.exact_range,
        ),
        relation_type="direct_call",
        strength="strong",
        quality="exact",
        evidence_refs=(helper.declaration_id, "fixture:call-occurrence"),
        provenance_ref="fixture:bounded-relation-query",
    )
    helper_text = support.content.splitlines(keepends=True)[0]
    candidate = ContextCandidate.create(
        primary_unit_id=primary.unit_id,
        review_question_id="RQ-correctness",
        relation_edge_id=edge.edge_id,
        relation_type="direct_call",
        target_source_ref_id=support.source_ref.source_ref_id,
        target_span=helper.exact_range,
        estimated_tokens=estimate_code_tokens(helper_text),
        necessity="required",
        provenance_ref=helper.declaration_id,
    )

    plan = analyzer.plan_context(
        analysis,
        primary_question_bindings=tuple(
            QuestionBinding(unit.unit_id, "RQ-correctness")
            for unit in analysis.review_units
        ),
        source_snapshots=context_snapshots,
        supporting_file_analyses=(support_parse_result.analysis,),
        candidates=(candidate,),
        relation_edges=(edge,),
        code_context_budget=1000,
    )

    assert len(plan.supporting_segments) == 1
    assert plan.supporting_segments[0].source_text == helper_text

    unsafe_span = ExactRange(
        2,
        2,
        helper.exact_range.start_offset_utf16 + 9,
        helper.exact_range.end_offset_utf16 - 2,
    )
    unsafe_edge = RelationEdge.create(
        source_ref=primary.unit_id,
        target_ref=source_span_ref_id(
            support.source_ref.source_ref_id,
            unsafe_span,
        ),
        relation_type="direct_call",
        strength="strong",
        quality="exact",
        evidence_refs=(helper.declaration_id, "fixture:call-occurrence"),
        provenance_ref="fixture:bounded-relation-query",
    )
    unsafe_text = "helper() { return 2 "
    unsafe_candidate = ContextCandidate.create(
        primary_unit_id=primary.unit_id,
        review_question_id="RQ-correctness",
        relation_edge_id=unsafe_edge.edge_id,
        relation_type="direct_call",
        target_source_ref_id=support.source_ref.source_ref_id,
        target_span=unsafe_span,
        estimated_tokens=estimate_code_tokens(unsafe_text),
        necessity="required",
        provenance_ref=helper.declaration_id,
    )
    with pytest.raises(ValueError, match="does not equal its occurrence boundary"):
        analyzer.plan_context(
            analysis,
            primary_question_bindings=tuple(
                QuestionBinding(unit.unit_id, "RQ-correctness")
                for unit in analysis.review_units
            ),
            source_snapshots=context_snapshots,
            supporting_file_analyses=(support_parse_result.analysis,),
            candidates=(unsafe_candidate,),
            relation_edges=(unsafe_edge,),
            code_context_budget=1000,
        )

    warning_analysis = replace(
        support_parse_result.analysis,
        parser_quality=FileParserQuality(
            layer="L1",
            error_nodes=1,
            missing_nodes=0,
            warnings=("parser_error_nodes",),
        ),
    )
    with pytest.raises(ValueError, match="requires a degraded relation"):
        analyzer.plan_context(
            analysis,
            primary_question_bindings=tuple(
                QuestionBinding(unit.unit_id, "RQ-correctness")
                for unit in analysis.review_units
            ),
            source_snapshots=context_snapshots,
            supporting_file_analyses=(warning_analysis,),
            candidates=(candidate,),
            relation_edges=(edge,),
            code_context_budget=1000,
        )


def test_binary_change_blocks_empty_context_plan() -> None:
    change_set = normalize_change_set(
        repository="repo",
        base_revision="base",
        head_revision="head",
        files=(
            ChangedFileInput(
                status="modified",
                old_path="assets/a.bin",
                new_path="assets/a.bin",
                is_binary=True,
            ),
        ),
    )
    analyzer = CodeAnalyzer(file_parser=CountingFileParser())
    analysis = analyzer.analyze_change_set(change_set, {})

    plan = analyzer.plan_context(
        analysis,
        primary_question_bindings=(),
        source_snapshots=(),
        code_context_budget=100,
    )

    assert plan.bundles == ()
    assert [item.code for item in plan.diagnostics] == ["context_insufficient"]
    assert plan.diagnostics[0].subject_ids == (
        change_set.files[0].changed_file_id,
    )


def test_plan_context_rejects_legacy_analysis_result() -> None:
    analyzer = CodeAnalyzer(parser=LexicalParser())
    analysis = analyzer.analyze_file(
        "src/A.ets",
        "function changed() {}\n",
    )

    with pytest.raises(ValueError, match="review-unit-build-v3"):
        analyzer.plan_context(
            analysis,
            primary_question_bindings=tuple(
                QuestionBinding(unit.unit_id, "RQ-correctness")
                for unit in analysis.review_units
            ),
            source_snapshots=(),
            code_context_budget=100,
        )
