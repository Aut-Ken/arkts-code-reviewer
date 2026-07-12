from __future__ import annotations

import hashlib

import pytest

from arkts_code_reviewer.code_analysis.change_review import (
    CHANGE_REVIEW_BUILD_SCHEMA_VERSION,
    ChangeSetReviewUnitBuilder,
    build_change_review_units,
)
from arkts_code_reviewer.code_analysis.change_set import (
    ChangeAtom,
    ChangeAtomInput,
    ChangedFile,
    ChangedFileInput,
    ChangeSet,
    CodeSourceSnapshot,
    normalize_change_set,
)
from arkts_code_reviewer.code_analysis.file_analysis_models import (
    CodeSourceRef,
    DeclarationOccurrence,
    ExactRange,
    FileAnalysis,
    FileParseResult,
    FileParserQuality,
    ReviewRegion,
    ScopedFacts,
)
from arkts_code_reviewer.code_analysis.models import (
    CodeFacts,
    Declaration,
    FileHunk,
    ParserQuality,
    ReviewUnitFileResult,
    ReviewUnitSpan,
    SourceSpan,
)
from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder

REPOSITORY = "repo"
BASE = "base-revision"
HEAD = "head-revision"


def _snapshot(path: str, content: str, revision: str) -> CodeSourceSnapshot:
    content_hash = f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"
    return CodeSourceSnapshot(
        source_ref=CodeSourceRef.create(
            repository=REPOSITORY,
            revision=revision,
            path=path,
            content_hash=content_hash,
        ),
        content=content,
    )


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _exact_range(
    snapshot: CodeSourceSnapshot,
    start_line: int,
    end_line: int,
) -> ExactRange:
    lines = snapshot.content.splitlines(keepends=True)
    start = sum(_utf16_length(line) for line in lines[: start_line - 1])
    end = start + sum(
        _utf16_length(line) for line in lines[start_line - 1 : end_line]
    )
    return ExactRange(start_line, end_line, start, end)


def _declaration(
    snapshot: CodeSourceSnapshot,
    kind: str,
    name: str,
    qualified_name: str,
    start_line: int,
    end_line: int,
    *,
    parent_id: str | None = None,
) -> DeclarationOccurrence:
    return DeclarationOccurrence.create(
        source_ref_id=snapshot.source_ref.source_ref_id,
        kind=kind,
        name=name,
        qualified_name=qualified_name,
        span=SourceSpan(start_line, end_line),
        exact_range=_exact_range(snapshot, start_line, end_line),
        parent_id=parent_id,
    )


def _region(
    snapshot: CodeSourceSnapshot,
    kind: str,
    symbol: str,
    start_line: int,
    end_line: int,
    *,
    owner_declaration_id: str | None = None,
) -> ReviewRegion:
    return ReviewRegion.create(
        source_ref_id=snapshot.source_ref.source_ref_id,
        kind=kind,  # type: ignore[arg-type]
        symbol=symbol,
        span=SourceSpan(start_line, end_line),
        exact_range=_exact_range(snapshot, start_line, end_line),
        owner_declaration_id=owner_declaration_id,
    )


def _parse_result(
    snapshot: CodeSourceSnapshot,
    *,
    declarations: tuple[DeclarationOccurrence, ...] = (),
    regions: tuple[ReviewRegion, ...] = (),
    layer: str = "L1",
    warnings: tuple[str, ...] = (),
) -> FileParseResult:
    declarations = tuple(
        sorted(
            declarations,
            key=lambda item: (
                item.span.start_line,
                item.exact_range.start_offset_utf16,
                item.span.end_line,
                item.exact_range.end_offset_utf16,
                item.kind,
                item.qualified_name,
                item.declaration_id,
            ),
        )
    )
    regions = tuple(
        sorted(
            regions,
            key=lambda item: (
                item.span.start_line,
                item.exact_range.start_offset_utf16,
                item.span.end_line,
                item.exact_range.end_offset_utf16,
                item.kind,
                item.symbol,
                item.region_id,
            ),
        )
    )
    facts = CodeFacts(
        path=snapshot.source_ref.path,
        declarations=[
            Declaration(
                kind=item.kind,  # type: ignore[arg-type]
                name=item.name,
                qualified_name=item.qualified_name,
                span=item.span,
                text="\n".join(
                    snapshot.content.splitlines()[
                        item.span.start_line - 1 : item.span.end_line
                    ]
                ),
                declaration_id=item.declaration_id,
                parent_id=item.parent_id,
                start_offset_utf16=item.exact_range.start_offset_utf16,
                end_offset_utf16=item.exact_range.end_offset_utf16,
            )
            for item in declarations
        ],
        parser_layer=layer,  # type: ignore[arg-type]
        warnings=list(warnings),
    )
    analysis = FileAnalysis.create(
        source_ref=snapshot.source_ref,
        parser_version="test-parser-v2",
        parser_quality=FileParserQuality(
            layer=layer,  # type: ignore[arg-type]
            error_nodes=0 if layer == "L1" else None,
            missing_nodes=0 if layer == "L1" else None,
            warnings=warnings,
        ),
        file_hints=ScopedFacts.from_code_facts(facts),
        declarations=declarations,
        review_regions=regions,
    )
    return FileParseResult(analysis=analysis, compatibility_facts=facts)


def _tables(
    *pairs: tuple[CodeSourceSnapshot, FileParseResult],
) -> tuple[dict[str, CodeSourceSnapshot], dict[str, FileParseResult]]:
    return (
        {pair[0].source_ref.source_ref_id: pair[0] for pair in pairs},
        {pair[0].source_ref.source_ref_id: pair[1] for pair in pairs},
    )


def test_replacement_builds_independent_base_and_head_units() -> None:
    base = _snapshot(
        "src/Page.ets",
        "struct Page {\n  build() {\n    Text('old')\n  }\n}\n",
        BASE,
    )
    head = _snapshot(
        "src/Page.ets",
        "struct Page {\n  build() {\n    Text('new')\n  }\n}\n",
        HEAD,
    )
    base_host = _declaration(base, "struct", "Page", "Page", 1, 5)
    base_build = _declaration(
        base,
        "build_method",
        "build",
        "Page.build",
        2,
        4,
        parent_id=base_host.declaration_id,
    )
    head_host = _declaration(head, "struct", "Page", "Page", 1, 5)
    head_build = _declaration(
        head,
        "build_method",
        "build",
        "Page.build",
        2,
        4,
        parent_id=head_host.declaration_id,
    )
    change_set = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path="src/Page.ets",
                new_path="src/Page.ets",
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="replacement",
                        old_span=ReviewUnitSpan(2, 4),
                        new_span=ReviewUnitSpan(2, 4),
                        deleted_old_lines=(3,),
                        added_new_lines=(3,),
                    ),
                ),
            ),
        ),
    )
    base_parse = _parse_result(base, declarations=(base_host, base_build))
    head_parse = _parse_result(head, declarations=(head_host, head_build))
    snapshots, parses = _tables((base, base_parse), (head, head_parse))

    result = build_change_review_units(
        change_set=change_set,
        source_snapshots=snapshots,
        file_parse_results=parses,
        review_unit_builder=ReviewUnitBuilder(),
    )

    assert result.schema_version == CHANGE_REVIEW_BUILD_SCHEMA_VERSION
    assert [item.source_role for item in result.file_results] == ["base", "head"]
    base_unit = result.file_results[0].units[0]
    head_unit = result.file_results[1].units[0]
    assert base_unit.changed_old_lines == [3]
    assert base_unit.changed_new_lines == []
    assert head_unit.changed_old_lines == []
    assert head_unit.changed_new_lines == [3]
    assert base_unit.change_atom_ids == head_unit.change_atom_ids == [
        change_set.atoms[0].atom_id
    ]
    assert ":Rbase:S" in base_unit.unit_id
    assert ":Rhead:S" in head_unit.unit_id
    assert base_unit.unit_id != head_unit.unit_id
    assert base_unit.full_text == "  build() {\n    Text('old')\n  }"
    assert head_unit.full_text == "  build() {\n    Text('new')\n  }"


def test_sparse_lines_and_multiple_atoms_merge_without_marking_context() -> None:
    base = _snapshot("src/A.ets", "old\n", BASE)
    head = _snapshot(
        "src/A.ets",
        "function run() {\n  first()\n  keep()\n  keepAgain()\n  second()\n}\n",
        HEAD,
    )
    function = _declaration(head, "function", "run", "run", 1, 6)
    change_set = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path="src/A.ets",
                new_path="src/A.ets",
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="addition",
                        old_span=None,
                        new_span=ReviewUnitSpan(1, 6),
                        added_new_lines=(2,),
                    ),
                    ChangeAtomInput(
                        kind="addition",
                        old_span=None,
                        new_span=ReviewUnitSpan(1, 6),
                        added_new_lines=(5,),
                    ),
                ),
            ),
        ),
    )
    snapshots, parses = _tables(
        (base, _parse_result(base)),
        (head, _parse_result(head, declarations=(function,))),
    )

    result = ChangeSetReviewUnitBuilder(ReviewUnitBuilder()).build(
        change_set,
        snapshots,
        parses,
    )

    head_result = next(item for item in result.file_results if item.source_role == "head")
    assert len(head_result.units) == 1
    unit = head_result.units[0]
    assert unit.changed_new_lines == [2, 5]
    assert unit.file_changed_lines == [2, 5]
    assert unit.unit_changed_lines == [2, 5]
    assert len(unit.change_atom_ids) == 2
    assert 3 not in unit.changed_new_lines
    assert 4 not in unit.changed_new_lines


def test_review_regions_take_priority_over_containing_declaration() -> None:
    base = _snapshot("src/Regions.ets", "old\n", BASE)
    head = _snapshot(
        "src/Regions.ets",
        "import {\n  X\n} from 'm'\nstruct Page {\n  value: number = 1\n  build() {}\n}\n",
        HEAD,
    )
    host = _declaration(head, "struct", "Page", "Page", 4, 7)
    import_region = _region(head, "import_region", "import:m", 1, 3)
    field_region = _region(
        head,
        "field_region",
        "Page.value",
        5,
        5,
        owner_declaration_id=host.declaration_id,
    )
    change_set = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path="src/Regions.ets",
                new_path="src/Regions.ets",
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="addition",
                        old_span=None,
                        new_span=ReviewUnitSpan(1, 3),
                        added_new_lines=(2,),
                    ),
                    ChangeAtomInput(
                        kind="addition",
                        old_span=None,
                        new_span=ReviewUnitSpan(5, 5),
                        added_new_lines=(5,),
                    ),
                ),
            ),
        ),
    )
    snapshots, parses = _tables(
        (base, _parse_result(base)),
        (
            head,
            _parse_result(
                head,
                declarations=(host,),
                regions=(import_region, field_region),
            ),
        ),
    )

    result = ChangeSetReviewUnitBuilder(ReviewUnitBuilder()).build(
        change_set,
        snapshots,
        parses,
    )

    head_units = next(
        item.units for item in result.file_results if item.source_role == "head"
    )
    assert [unit.unit_kind for unit in head_units] == [
        "import_region",
        "field_region",
    ]
    assert all(unit.selection_reason == "changed_review_region" for unit in head_units)
    assert all(unit.owner_ref is not None for unit in head_units)
    assert all(unit.owner_ref.kind == "region" for unit in head_units if unit.owner_ref)


def test_same_line_non_nested_regions_are_all_retained() -> None:
    base = _snapshot("src/Ambiguous.ets", "old\n", BASE)
    head = _snapshot(
        "src/Ambiguous.ets",
        "struct Page {\n  left: number = right\n}\n",
        HEAD,
    )
    host = _declaration(head, "struct", "Page", "Page", 1, 3)
    left = _region(
        head,
        "field_region",
        "Page.left",
        2,
        2,
        owner_declaration_id=host.declaration_id,
    )
    right = _region(
        head,
        "field_region",
        "Page.right",
        2,
        2,
        owner_declaration_id=host.declaration_id,
    )
    change_set = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path="src/Ambiguous.ets",
                new_path="src/Ambiguous.ets",
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="addition",
                        old_span=None,
                        new_span=ReviewUnitSpan(2, 2),
                        added_new_lines=(2,),
                    ),
                ),
            ),
        ),
    )
    snapshots, parses = _tables(
        (base, _parse_result(base)),
        (
            head,
            _parse_result(
                head,
                declarations=(host,),
                regions=(left, right),
            ),
        ),
    )

    result = ChangeSetReviewUnitBuilder(ReviewUnitBuilder()).build(
        change_set,
        snapshots,
        parses,
    )

    units = next(item.units for item in result.file_results if item.source_role == "head")
    assert [unit.unit_symbol for unit in units] == ["Page.left", "Page.right"]
    assert [unit.changed_new_lines for unit in units] == [[2], [2]]
    assert all(unit.change_atom_ids == [change_set.atoms[0].atom_id] for unit in units)


def test_long_build_retains_same_line_duplicate_ui_occurrence_identity() -> None:
    base_lines = ["struct Page {", "  build() {"]
    base_lines.extend("    Blank()" for _ in range(3, 162))
    base_lines.extend(["  }", "}"])
    head_lines = list(base_lines)
    head_lines[99] = "    Text('a'); Text('b')"
    base = _snapshot("src/Duplicate.ets", "\n".join(base_lines) + "\n", BASE)
    head = _snapshot("src/Duplicate.ets", "\n".join(head_lines) + "\n", HEAD)
    source_lines = head.content.splitlines(keepends=True)
    line100_start = sum(_utf16_length(line) for line in source_lines[:99])
    first_start = line100_start + _utf16_length("    ")
    first_end = first_start + _utf16_length("Text('a')")
    second_start = line100_start + _utf16_length("    Text('a'); ")
    second_end = second_start + _utf16_length("Text('b')")
    host = DeclarationOccurrence.create(
        source_ref_id=head.source_ref.source_ref_id,
        kind="struct",
        name="Page",
        qualified_name="Page",
        span=SourceSpan(1, 163),
        exact_range=ExactRange(1, 163, 0, _utf16_length(head.content)),
    )
    build = DeclarationOccurrence.create(
        source_ref_id=head.source_ref.source_ref_id,
        kind="build_method",
        name="build",
        qualified_name="Page.build",
        span=SourceSpan(2, 162),
        exact_range=ExactRange(
            2,
            162,
            _utf16_length(source_lines[0]),
            sum(_utf16_length(line) for line in source_lines[:162]),
        ),
        parent_id=host.declaration_id,
    )
    first = DeclarationOccurrence.create(
        source_ref_id=head.source_ref.source_ref_id,
        kind="ui_block",
        name="Text",
        qualified_name="Page.build.Text",
        span=SourceSpan(100, 100),
        exact_range=ExactRange(100, 100, first_start, first_end),
        parent_id=build.declaration_id,
    )
    second = DeclarationOccurrence.create(
        source_ref_id=head.source_ref.source_ref_id,
        kind="ui_block",
        name="Text",
        qualified_name="Page.build.Text",
        span=SourceSpan(100, 100),
        exact_range=ExactRange(100, 100, second_start, second_end),
        parent_id=build.declaration_id,
    )
    change_set = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path="src/Duplicate.ets",
                new_path="src/Duplicate.ets",
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="addition",
                        old_span=None,
                        new_span=ReviewUnitSpan(100, 100),
                        added_new_lines=(100,),
                    ),
                ),
            ),
        ),
    )
    snapshots, parses = _tables(
        (base, _parse_result(base)),
        (
            head,
            _parse_result(
                head,
                declarations=(host, build, first, second),
            ),
        ),
    )

    result = ChangeSetReviewUnitBuilder(ReviewUnitBuilder()).build(
        change_set,
        snapshots,
        parses,
    )

    units = next(item.units for item in result.file_results if item.source_role == "head")
    assert [unit.unit_kind for unit in units] == ["ui_block", "ui_block"]
    assert all(unit.changed_new_lines == [100] for unit in units)
    assert len({unit.unit_id for unit in units}) == 2
    assert all(":O" in unit.unit_id and ":Rhead:S" in unit.unit_id for unit in units)
    assert {unit.owner_ref.ref_id for unit in units if unit.owner_ref} == {
        first.declaration_id,
        second.declaration_id,
    }


def test_one_atom_crossing_two_methods_builds_both_owners() -> None:
    base = _snapshot("src/Methods.ets", "old\n", BASE)
    head = _snapshot(
        "src/Methods.ets",
        "function first() {\n  changedOne()\n}\nfunction second() {\n  changedTwo()\n}\n",
        HEAD,
    )
    first = _declaration(head, "function", "first", "first", 1, 3)
    second = _declaration(head, "function", "second", "second", 4, 6)
    change_set = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path="src/Methods.ets",
                new_path="src/Methods.ets",
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="addition",
                        old_span=None,
                        new_span=ReviewUnitSpan(2, 5),
                        added_new_lines=(2, 5),
                    ),
                ),
            ),
        ),
    )
    snapshots, parses = _tables(
        (base, _parse_result(base)),
        (head, _parse_result(head, declarations=(first, second))),
    )

    result = ChangeSetReviewUnitBuilder(ReviewUnitBuilder()).build(
        change_set,
        snapshots,
        parses,
    )

    units = next(item.units for item in result.file_results if item.source_role == "head")
    assert [unit.unit_symbol for unit in units] == ["first", "second"]
    assert [unit.changed_new_lines for unit in units] == [[2], [5]]
    assert all(unit.change_atom_ids == [change_set.atoms[0].atom_id] for unit in units)


def test_deleted_file_uses_base_source_and_old_line_coordinates() -> None:
    base = _snapshot(
        "src/Deleted.ets",
        "function old() {\n  cleanup()\n}\n",
        BASE,
    )
    function = _declaration(base, "function", "old", "old", 1, 3)
    change_set = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="deleted",
                old_path="src/Deleted.ets",
                new_path=None,
                old_snapshot=base,
                atoms=(
                    ChangeAtomInput(
                        kind="deletion",
                        old_span=ReviewUnitSpan(1, 3),
                        new_span=None,
                        deleted_old_lines=(1, 2, 3),
                    ),
                ),
            ),
        ),
    )
    snapshots, parses = _tables(
        (base, _parse_result(base, declarations=(function,))),
    )

    result = ChangeSetReviewUnitBuilder(ReviewUnitBuilder()).build(
        change_set,
        snapshots,
        parses,
    )

    assert len(result.file_results) == 1
    file_result = result.file_results[0]
    assert file_result.source_role == "base"
    assert file_result.unassigned_hunk_lines == []
    assert file_result.unassigned_change_atom_ids == []
    assert len(file_result.units) == 1
    assert file_result.units[0].changed_old_lines == [1, 2, 3]
    assert file_result.units[0].changed_new_lines == []


def test_pure_rename_produces_empty_base_and_head_source_results() -> None:
    content = "struct Page {}\n"
    base = _snapshot("src/Page.ets", content, BASE)
    head = _snapshot("src/Renamed.ets", content, HEAD)
    change_set = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="renamed",
                old_path="src/Page.ets",
                new_path="src/Renamed.ets",
                old_snapshot=base,
                new_snapshot=head,
            ),
        ),
    )
    snapshots, parses = _tables(
        (base, _parse_result(base)),
        (head, _parse_result(head)),
    )

    result = ChangeSetReviewUnitBuilder(ReviewUnitBuilder()).build(
        change_set,
        snapshots,
        parses,
    )

    assert [item.source_role for item in result.file_results] == ["base", "head"]
    assert all(item.units == [] for item in result.file_results)
    assert result.flatten_units() == []


def test_binary_change_has_diagnostic_without_fabricated_source_or_unit() -> None:
    change_set = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path="assets/icon.bin",
                new_path="assets/icon.bin",
                is_binary=True,
            ),
        ),
    )

    result = ChangeSetReviewUnitBuilder(ReviewUnitBuilder()).build(
        change_set,
        {},
        {},
    )

    assert result.file_results == []
    assert result.flatten_units() == []
    assert [item.code for item in result.diagnostics] == [
        "binary_change_unsupported"
    ]


def test_parse_degraded_source_falls_back_with_structured_quality() -> None:
    base = _snapshot("src/Fallback.ets", "old\n", BASE)
    head = _snapshot("src/Fallback.ets", "changed\n", HEAD)
    change_set = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path="src/Fallback.ets",
                new_path="src/Fallback.ets",
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="replacement",
                        old_span=ReviewUnitSpan(1, 1),
                        new_span=ReviewUnitSpan(1, 1),
                        deleted_old_lines=(1,),
                        added_new_lines=(1,),
                    ),
                ),
            ),
        ),
    )
    snapshots, parses = _tables(
        (base, _parse_result(base, layer="parse_degraded")),
        (head, _parse_result(head, layer="parse_degraded")),
    )

    result = ChangeSetReviewUnitBuilder(ReviewUnitBuilder()).build(
        change_set,
        snapshots,
        parses,
    )

    assert len(result.flatten_units()) == 2
    for unit in result.flatten_units():
        assert unit.unit_kind == "fallback"
        assert unit.context_degraded is True
        assert {item.code for item in unit.diagnostics} == {
            "no_matching_declaration",
            "parser_degraded",
        }


def test_source_and_parse_tables_must_exactly_match_immutable_sources() -> None:
    head = _snapshot("src/New.ets", "new\n", HEAD)
    change_set = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="added",
                old_path=None,
                new_path="src/New.ets",
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
    parse_result = _parse_result(head)

    with pytest.raises(ValueError, match="exactly cover"):
        ChangeSetReviewUnitBuilder(ReviewUnitBuilder()).build(
            change_set,
            {},
            {head.source_ref.source_ref_id: parse_result},
        )

    drifted = _snapshot("src/New.ets", head.content, "other")
    with pytest.raises(ValueError, match="must match ChangeSet source"):
        ChangeSetReviewUnitBuilder(ReviewUnitBuilder()).build(
            change_set,
            {head.source_ref.source_ref_id: drifted},
            {head.source_ref.source_ref_id: parse_result},
        )


class _NoAssignmentBuilder(ReviewUnitBuilder):
    def build_file_result(  # type: ignore[override]
        self,
        path: str,
        source: str,
        facts: CodeFacts,
        mode: str,
        hunks: list[object],
        *,
        source_ref_id: str | None = None,
    ) -> ReviewUnitFileResult:
        return ReviewUnitFileResult(
            path=path,
            units=[],
            parser_quality=ParserQuality(
                parser_layer=facts.parser_layer,
                warnings=sorted(set(facts.warnings)),
            ),
            source_ref_id=source_ref_id,
        )


class _BaseOnlyNoAssignmentBuilder(ReviewUnitBuilder):
    def build_file_result(
        self,
        path: str,
        source: str,
        facts: CodeFacts,
        mode: str,
        hunks: list[FileHunk],
        *,
        source_ref_id: str | None = None,
    ) -> ReviewUnitFileResult:
        if "old" in source:
            return ReviewUnitFileResult(
                path=path,
                units=[],
                parser_quality=ParserQuality(
                    parser_layer=facts.parser_layer,
                    warnings=sorted(set(facts.warnings)),
                ),
                source_ref_id=source_ref_id,
            )
        return super().build_file_result(
            path,
            source,
            facts,
            mode,  # type: ignore[arg-type]
            hunks,
            source_ref_id=source_ref_id,
        )


def test_unassigned_atom_is_recorded_on_its_source_role_and_build() -> None:
    head = _snapshot("src/New.ets", "new\n", HEAD)
    change_set = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="added",
                old_path=None,
                new_path="src/New.ets",
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
    snapshots, parses = _tables((head, _parse_result(head)))

    result = ChangeSetReviewUnitBuilder(_NoAssignmentBuilder()).build(
        change_set,
        snapshots,
        parses,
    )

    atom_id = change_set.atoms[0].atom_id
    assert result.file_results[0].source_role == "head"
    assert result.file_results[0].unassigned_change_atom_ids == [atom_id]
    assert result.unassigned_change_atom_ids == [atom_id]
    assert [item.code for item in result.diagnostics] == [
        "change_atom_unassigned"
    ]


def test_replacement_can_degrade_one_source_role_without_dropping_the_other() -> None:
    base = _snapshot("src/Partial.ets", "old\n", BASE)
    head = _snapshot("src/Partial.ets", "new\n", HEAD)
    change_set = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path="src/Partial.ets",
                new_path="src/Partial.ets",
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="replacement",
                        old_span=ReviewUnitSpan(1, 1),
                        new_span=ReviewUnitSpan(1, 1),
                        deleted_old_lines=(1,),
                        added_new_lines=(1,),
                    ),
                ),
            ),
        ),
    )
    snapshots, parses = _tables(
        (base, _parse_result(base)),
        (head, _parse_result(head)),
    )

    result = ChangeSetReviewUnitBuilder(_BaseOnlyNoAssignmentBuilder()).build(
        change_set,
        snapshots,
        parses,
    )

    atom_id = change_set.atoms[0].atom_id
    base_result = next(item for item in result.file_results if item.source_role == "base")
    head_result = next(item for item in result.file_results if item.source_role == "head")
    assert base_result.units == []
    assert base_result.unassigned_change_atom_ids == [atom_id]
    assert head_result.unassigned_change_atom_ids == []
    assert head_result.units[0].change_atom_ids == [atom_id]
    assert head_result.units[0].changed_new_lines == [1]
    assert result.unassigned_change_atom_ids == [atom_id]


def test_rejects_change_atom_exact_range_that_does_not_match_snapshot() -> None:
    base = _snapshot("src/Range.ets", "keep\n", BASE)
    head = _snapshot("src/Range.ets", "keep\nnew\n", HEAD)
    valid = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path="src/Range.ets",
                new_path="src/Range.ets",
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="addition",
                        old_span=None,
                        new_span=ReviewUnitSpan(2, 2),
                        added_new_lines=(2,),
                    ),
                ),
            ),
        ),
    )
    original_atom = valid.atoms[0]
    drifted_atom = ChangeAtom.create(
        kind="addition",
        old_source_ref_id=None,
        new_source_ref_id=original_atom.new_source_ref_id,
        old_span=None,
        new_span=ExactRange(2, 2, 0, 1),
        added_new_lines=(2,),
        deleted_old_lines=(),
        diff_positions=(),
        diff_normalizer_version=valid.diff_normalizer_version,
    )
    original_file = valid.files[0]
    drifted_file = ChangedFile.create(
        status=original_file.status,
        old_path=original_file.old_path,
        new_path=original_file.new_path,
        old_source_ref_id=original_file.old_source_ref_id,
        new_source_ref_id=original_file.new_source_ref_id,
        atom_ids=(drifted_atom.atom_id,),
        is_binary=False,
    )
    drifted = ChangeSet.create(
        repository=valid.repository,
        base_revision=valid.base_revision,
        head_revision=valid.head_revision,
        diff_normalizer_version=valid.diff_normalizer_version,
        source_refs=valid.source_refs,
        files=(drifted_file,),
        atoms=(drifted_atom,),
        diagnostics=(),
    )
    snapshots, parses = _tables(
        (base, _parse_result(base)),
        (head, _parse_result(head)),
    )

    with pytest.raises(ValueError, match="exact range does not match"):
        ChangeSetReviewUnitBuilder(ReviewUnitBuilder()).build(
            drifted,
            snapshots,
            parses,
        )


def test_rejects_file_analysis_line_and_utf16_offset_drift() -> None:
    base = _snapshot("src/Range.ets", "keep\n", BASE)
    head = _snapshot("src/Range.ets", "keep\nnew\n", HEAD)
    change_set = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path="src/Range.ets",
                new_path="src/Range.ets",
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="addition",
                        old_span=None,
                        new_span=ReviewUnitSpan(2, 2),
                        added_new_lines=(2,),
                    ),
                ),
            ),
        ),
    )
    drifted_region = ReviewRegion.create(
        source_ref_id=head.source_ref.source_ref_id,
        kind="import_region",
        symbol="import:drifted",
        span=SourceSpan(2, 2),
        exact_range=ExactRange(2, 2, 0, 3),
        owner_declaration_id=None,
    )
    snapshots, parses = _tables(
        (base, _parse_result(base)),
        (head, _parse_result(head, regions=(drifted_region,))),
    )

    with pytest.raises(ValueError, match="start line/offset mismatch"):
        ChangeSetReviewUnitBuilder(ReviewUnitBuilder()).build(
            change_set,
            snapshots,
            parses,
        )


def test_rejects_manual_added_change_set_without_full_source_atoms() -> None:
    head = _snapshot("src/Manual.ets", "new\n", HEAD)
    changed_file = ChangedFile.create(
        status="added",
        old_path=None,
        new_path=head.source_ref.path,
        old_source_ref_id=None,
        new_source_ref_id=head.source_ref.source_ref_id,
        atom_ids=(),
        is_binary=False,
    )
    change_set = ChangeSet.create(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        diff_normalizer_version="change-normalizer-v1",
        source_refs=(head.source_ref,),
        files=(changed_file,),
        atoms=(),
        diagnostics=(),
    )
    snapshots, parses = _tables((head, _parse_result(head)))

    with pytest.raises(ValueError, match="complete head source"):
        ChangeSetReviewUnitBuilder(ReviewUnitBuilder()).build(
            change_set,
            snapshots,
            parses,
        )


def test_rejects_manual_pure_rename_with_different_content() -> None:
    base = _snapshot("src/Before.ets", "old\n", BASE)
    head = _snapshot("src/After.ets", "new\n", HEAD)
    changed_file = ChangedFile.create(
        status="renamed",
        old_path=base.source_ref.path,
        new_path=head.source_ref.path,
        old_source_ref_id=base.source_ref.source_ref_id,
        new_source_ref_id=head.source_ref.source_ref_id,
        atom_ids=(),
        is_binary=False,
    )
    change_set = ChangeSet.create(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        diff_normalizer_version="change-normalizer-v1",
        source_refs=tuple(
            sorted(
                (base.source_ref, head.source_ref),
                key=lambda item: item.source_ref_id,
            )
        ),
        files=(changed_file,),
        atoms=(),
        diagnostics=(),
    )
    snapshots, parses = _tables(
        (base, _parse_result(base)),
        (head, _parse_result(head)),
    )

    with pytest.raises(ValueError, match="at least one ChangeAtom"):
        ChangeSetReviewUnitBuilder(ReviewUnitBuilder()).build(
            change_set,
            snapshots,
            parses,
        )
