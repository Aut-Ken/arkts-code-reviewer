from __future__ import annotations

import hashlib

import pytest

from arkts_code_reviewer.code_analysis.analyzer import CodeAnalyzer
from arkts_code_reviewer.code_analysis.change_set import (
    ChangeAtomInput,
    ChangedFileInput,
    ChangeSet,
    CodeSourceSnapshot,
    normalize_change_set,
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

    result = CodeAnalyzer(
        file_parser=FixtureFileParser(parse_results)
    ).analyze_change_set(
        change_set,
        {
            base.source_ref.source_ref_id: base,
            head.source_ref.source_ref_id: head,
        },
    )

    base_retrieval, head_retrieval = result.retrieval_query.units
    assert base_retrieval.code_features.apis == ["router.pushUrl"]
    assert head_retrieval.code_features.apis == ["setInterval"]
    assert "has_network" not in base_retrieval.code_features.tags
    assert "has_network" not in head_retrieval.code_features.tags
    assert "has_network" in base_retrieval.routing_tags
    assert "has_network" in head_retrieval.routing_tags
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
