from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

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
    FileAnalysis,
    FileParseResult,
    FileParserQuality,
    OwnerRef,
    ScopedFacts,
    UnitFactScope,
)
from arkts_code_reviewer.code_analysis.models import (
    REVIEW_UNIT_BUILD_SCHEMA_VERSION,
    AnalysisMetadata,
    AnalysisResult,
    CodeFacts,
    CodeFeatures,
    MrContext,
    ParserQuality,
    RetrievalQuery,
    RetrievalUnit,
    ReviewUnit,
    ReviewUnitBuildResult,
    ReviewUnitFileResult,
    ReviewUnitSpan,
    SourceRole,
    SourceSpan,
)
from arkts_code_reviewer.code_analysis.review_unit_contract import (
    REVIEW_UNIT_DIAGNOSTIC_CODES,
    REVIEW_UNIT_KINDS,
    REVIEW_UNIT_V2_KINDS,
    REVIEW_UNIT_V2_SELECTION_REASONS,
    SELECTION_REASONS,
    ReviewUnitKind,
    declaration_unit_id,
)

PATH = "src/Same.ets"
REPOSITORY = "fixture-repository"
BASE_REVISION = "base-revision"
HEAD_REVISION = "head-revision"
BASE_SOURCE = "function same() {\n  return 1\n}\n"
HEAD_SOURCE = "function same() {\n  return 2\n}\n"
GOLDEN_MANIFEST = (
    Path(__file__).resolve().parent / "golden" / "review_unit" / "manifest.json"
)


def _snapshot(content: str, revision: str) -> CodeSourceSnapshot:
    content_hash = f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"
    return CodeSourceSnapshot(
        source_ref=CodeSourceRef.create(
            repository=REPOSITORY,
            revision=revision,
            path=PATH,
            content_hash=content_hash,
        ),
        content=content,
    )


def _replacement_change_set(
    *,
    normalizer_version: str = "fixture-normalizer-v1",
) -> tuple[ChangeSet, CodeSourceSnapshot, CodeSourceSnapshot]:
    base = _snapshot(BASE_SOURCE, BASE_REVISION)
    head = _snapshot(HEAD_SOURCE, HEAD_REVISION)
    change_set = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE_REVISION,
        head_revision=HEAD_REVISION,
        diff_normalizer_version=normalizer_version,
        files=(
            ChangedFileInput(
                status="modified",
                old_path=PATH,
                new_path=PATH,
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="replacement",
                        old_span=ReviewUnitSpan(2, 2),
                        new_span=ReviewUnitSpan(2, 2),
                        deleted_old_lines=(2,),
                        added_new_lines=(2,),
                    ),
                ),
            ),
        ),
    )
    return change_set, base, head


def _changed_unit(
    *,
    role: SourceRole,
    source_ref: CodeSourceRef,
    source: str,
    atom_id: str,
) -> ReviewUnit:
    changed_old_lines = [2] if role == "base" else []
    changed_new_lines = [2] if role == "head" else []
    return ReviewUnit(
        file=PATH,
        unit_symbol="same",
        unit_ref=f"same@{PATH}",
        full_text=source.rstrip("\n"),
        changed_lines=[2],
        file_changed_lines=[2],
        unit_changed_lines=[2],
        unit_id=declaration_unit_id(
            PATH,
            "function",
            "same",
            1,
            3,
            source_role=role,
            source_ref_id=source_ref.source_ref_id,
        ),
        unit_kind="function",
        source_span=ReviewUnitSpan(1, 3),
        context_span=ReviewUnitSpan(1, 3),
        changed_new_lines=changed_new_lines,
        changed_old_lines=changed_old_lines,
        selection_reason="innermost_changed_declaration",
        diagnostics=[],
        source_ref_id=source_ref.source_ref_id,
        source_role=role,
        change_atom_ids=[atom_id],
        owner_ref=OwnerRef("declaration", _fixture_declaration(source_ref).declaration_id),
        identity_source_ref_id=source_ref.source_ref_id,
    )


def _file_result(
    unit: ReviewUnit,
    *,
    changed_file_id: str,
) -> ReviewUnitFileResult:
    assert unit.source_ref_id is not None
    assert unit.source_role is not None
    return ReviewUnitFileResult(
        path=unit.file,
        units=[unit],
        parser_quality=ParserQuality("L1"),
        source_ref_id=unit.source_ref_id,
        source_role=unit.source_role,
        changed_file_id=changed_file_id,
    )


def _build_fixture() -> tuple[
    ChangeSet,
    CodeSourceSnapshot,
    CodeSourceSnapshot,
    ReviewUnit,
    ReviewUnit,
    ReviewUnitBuildResult,
]:
    change_set, base, head = _replacement_change_set()
    atom_id = change_set.atoms[0].atom_id
    base_unit = _changed_unit(
        role="base",
        source_ref=base.source_ref,
        source=base.content,
        atom_id=atom_id,
    )
    head_unit = _changed_unit(
        role="head",
        source_ref=head.source_ref,
        source=head.content,
        atom_id=atom_id,
    )
    changed_file_id = change_set.files[0].changed_file_id
    file_results = [
        _file_result(base_unit, changed_file_id=changed_file_id),
        _file_result(head_unit, changed_file_id=changed_file_id),
    ]
    build = ReviewUnitBuildResult(
        schema_version="review-unit-build-v3",
        mode="diff",
        file_results=file_results,
        change_set_id=change_set.change_set_id,
    )
    return change_set, base, head, base_unit, head_unit, build


def _parse_result(source_ref: CodeSourceRef) -> FileParseResult:
    declaration = _fixture_declaration(source_ref)
    analysis = FileAnalysis.create(
        source_ref=source_ref,
        parser_version="fixture-parser-v1",
        parser_quality=FileParserQuality(
            layer="L1",
            error_nodes=0,
            missing_nodes=0,
        ),
        file_hints=ScopedFacts(),
        declarations=(declaration,),
    )
    return FileParseResult(
        analysis=analysis,
        compatibility_facts=CodeFacts(path=source_ref.path, parser_layer="L1"),
    )


def _fixture_declaration(source_ref: CodeSourceRef) -> DeclarationOccurrence:
    source = BASE_SOURCE if source_ref.revision == BASE_REVISION else HEAD_SOURCE
    return DeclarationOccurrence.create(
        source_ref_id=source_ref.source_ref_id,
        kind="function",
        name="same",
        qualified_name="same",
        span=SourceSpan(1, 3),
        exact_range=ExactRange(
            1,
            3,
            0,
            len(source.encode("utf-16-le")) // 2,
        ),
    )
def _analysis_fixture() -> tuple[AnalysisResult, ChangeSet]:
    change_set, base, head, base_unit, head_unit, build = _build_fixture()
    units = [base_unit, head_unit]
    scopes = [
        UnitFactScope(
            unit_id=unit.unit_id,
            source_ref_id=unit.source_ref_id or "",
            unit_exact=ScopedFacts(),
            file_hints=ScopedFacts(),
        )
        for unit in units
    ]
    from arkts_code_reviewer.feature_routing.engine import FeatureRouter

    feature_routing_result = FeatureRouter().route(scopes)
    profiles_by_unit = {
        profile.unit_id: profile for profile in feature_routing_result.units
    }
    retrieval_units = [
        RetrievalUnit(
            unit_ref=unit.unit_ref,
            code_features=CodeFeatures(
                tags=list(profiles_by_unit[unit.unit_id].exact_tags)
            ),
            intent_summary="fixture",
            unit_id=unit.unit_id,
            source_ref_id=unit.source_ref_id,
            unit_fact_scope=scope,
            dimensions=list(profiles_by_unit[unit.unit_id].dimensions),
            routing_tags=list(profiles_by_unit[unit.unit_id].routing_tags),
        )
        for unit, scope in zip(units, scopes, strict=True)
    ]
    result = AnalysisResult(
        retrieval_query=RetrievalQuery(
            mr_context=MrContext(
                triggered_dimensions=list(feature_routing_result.mr_dimensions),
                token_budget=1024,
            ),
            units=retrieval_units,
        ),
        review_units=units,
        metadata=AnalysisMetadata(parser_layer="L1"),
        review_unit_build_result=build,
        file_parse_results=[
            _parse_result(base.source_ref),
            _parse_result(head.source_ref),
        ],
        unit_fact_scopes=scopes,
        change_set=change_set,
        feature_routing_result=feature_routing_result,
    )
    return result, change_set


def test_review_unit_v1_contract_and_golden_identity_remain_frozen() -> None:
    assert REVIEW_UNIT_KINDS == (
        "struct",
        "class",
        "function",
        "method",
        "build_method",
        "builder",
        "ui_block",
        "fallback",
    )
    assert SELECTION_REASONS == (
        "full_top_level_declaration",
        "innermost_changed_declaration",
        "large_build_ui_block",
        "fallback_window",
    )
    assert REVIEW_UNIT_DIAGNOSTIC_CODES == (
        "budget_not_enforced",
        "changed_lines_outside_context",
        "diff_file_without_hunks",
        "hunk_out_of_range",
        "no_matching_declaration",
        "parser_degraded",
        "parser_error_nodes",
        "parser_missing_nodes",
        "unsupported_deletion_only",
    )
    assert declaration_unit_id(
        "src/pages/Main.ets",
        "ui_block",
        "Page.build.Column",
        40,
        58,
    ) == "src/pages/Main.ets@ui_block:Page.build.Column:L40-L58"

    manifest = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["suite_id"] == "review-unit-golden-v1"
    assert manifest["frozen_contract"] == {
        "unit_kinds": list(REVIEW_UNIT_KINDS),
        "selection_reasons": list(SELECTION_REASONS),
        "diagnostic_codes": list(REVIEW_UNIT_DIAGNOSTIC_CODES),
    }


@pytest.mark.parametrize("unit_kind", ["field_region", "import_region"])
def test_review_unit_v2_accepts_typed_review_regions(unit_kind: ReviewUnitKind) -> None:
    assert unit_kind in REVIEW_UNIT_V2_KINDS
    assert "changed_review_region" in REVIEW_UNIT_V2_SELECTION_REASONS
    source_ref = CodeSourceRef.inline(PATH, BASE_SOURCE)
    symbol = f"same.{unit_kind}"
    unit = ReviewUnit(
        file=PATH,
        unit_symbol=symbol,
        unit_ref=f"{symbol}@{PATH}",
        full_text=BASE_SOURCE.rstrip("\n"),
        unit_id=declaration_unit_id(
            PATH,
            unit_kind,
            symbol,
            1,
            3,
        ),
        unit_kind=unit_kind,
        source_span=ReviewUnitSpan(1, 3),
        context_span=ReviewUnitSpan(1, 3),
        changed_new_lines=[],
        selection_reason="changed_review_region",
        diagnostics=[],
        source_ref_id=source_ref.source_ref_id,
        owner_ref=OwnerRef("region", f"region:fixture:{unit_kind}"),
    )

    assert unit.unit_kind == unit_kind
    assert unit.selection_reason == "changed_review_region"


def test_change_set_base_and_head_units_have_collision_safe_source_identity() -> None:
    _, base, head, base_unit, head_unit, _ = _build_fixture()
    base_digest = base.source_ref.source_ref_id.removeprefix("code-source:sha256:")
    head_digest = head.source_ref.source_ref_id.removeprefix("code-source:sha256:")

    assert base_unit.unit_id.endswith(f":Rbase:S{base_digest}")
    assert head_unit.unit_id.endswith(f":Rhead:S{head_digest}")
    assert len(base_digest) == len(head_digest) == 64
    assert base_unit.unit_id != head_unit.unit_id
    assert base_unit.changed_old_lines == [2]
    assert base_unit.changed_new_lines == []
    assert head_unit.changed_old_lines == []
    assert head_unit.changed_new_lines == [2]


def test_changed_unit_requires_nonempty_sorted_change_atom_ids() -> None:
    _, _, _, base_unit, _, _ = _build_fixture()
    assert base_unit.change_atom_ids
    with pytest.raises(ValueError, match="at least one change_atom_id"):
        replace(base_unit, change_atom_ids=[])
    with pytest.raises(ValueError, match="sorted and unique"):
        replace(
            base_unit,
            change_atom_ids=[
                f"change-atom:sha256:{'f' * 64}",
                f"change-atom:sha256:{'0' * 64}",
            ],
        )


def test_file_result_enforces_unit_file_role_and_source_identity() -> None:
    change_set, _, _, base_unit, head_unit, _ = _build_fixture()
    changed_file_id = change_set.files[0].changed_file_id
    result = _file_result(base_unit, changed_file_id=changed_file_id)

    assert result.path == base_unit.file
    assert result.source_role == base_unit.source_role == "base"
    assert result.source_ref_id == base_unit.source_ref_id
    with pytest.raises(ValueError, match="source_ref_id|source_role"):
        replace(result, units=[head_unit])
    with pytest.raises(ValueError, match="belong to the result path"):
        replace(result, path="src/Other.ets")


def test_review_unit_build_v3_allows_base_and_head_for_the_same_path() -> None:
    change_set, _, _, base_unit, head_unit, build = _build_fixture()

    assert [result.path for result in build.file_results] == [PATH, PATH]
    assert [result.source_role for result in build.file_results] == ["base", "head"]
    assert build.change_set_id == change_set.change_set_id
    assert build.flatten_units() == [base_unit, head_unit]
    assert len({unit.unit_id for unit in build.flatten_units()}) == 2

    with pytest.raises(ValueError, match="requires a deterministic change_set_id"):
        replace(build, change_set_id=None)
    with pytest.raises(ValueError, match="changed-file/source order"):
        replace(build, file_results=list(reversed(build.file_results)))
    with pytest.raises(ValueError, match="mode must be diff"):
        replace(build, mode="full")


@pytest.mark.parametrize(
    "schema_version,source_ref_required",
    [
        (REVIEW_UNIT_BUILD_SCHEMA_VERSION, False),
        ("review-unit-build-v2", True),
    ],
)
def test_legacy_build_schemas_reject_change_set_fields(
    schema_version: str,
    source_ref_required: bool,
) -> None:
    change_set, base, _, base_unit, _, _ = _build_fixture()
    legacy_result = ReviewUnitFileResult(
        path=PATH,
        units=[],
        parser_quality=ParserQuality("L1"),
        source_ref_id=(base.source_ref.source_ref_id if source_ref_required else None),
    )
    with pytest.raises(ValueError, match="legacy.*ChangeSet fields"):
        ReviewUnitBuildResult(
            schema_version=schema_version,
            mode="diff",
            file_results=[legacy_result],
            change_set_id=change_set.change_set_id,
        )
    with pytest.raises(ValueError, match="legacy.*ChangeSet fields"):
        ReviewUnitBuildResult(
            schema_version=schema_version,
            mode="diff",
            file_results=[legacy_result],
            unassigned_change_atom_ids=[change_set.atoms[0].atom_id],
        )

    change_result = _file_result(
        base_unit,
        changed_file_id=change_set.files[0].changed_file_id,
    )
    with pytest.raises(ValueError, match="legacy.*ChangeSet fields"):
        ReviewUnitBuildResult(
            schema_version=schema_version,
            mode="diff",
            file_results=[change_result],
        )


def test_analysis_result_v3_requires_matching_change_set() -> None:
    result, change_set = _analysis_fixture()

    assert result.change_set is change_set
    build = result.review_unit_build_result
    assert build is not None
    assert build.change_set_id == change_set.change_set_id
    with pytest.raises(ValueError, match="requires ChangeSet"):
        replace(result, change_set=None)

    different_change_set, _, _ = _replacement_change_set(
        normalizer_version="fixture-normalizer-v2"
    )
    with pytest.raises(ValueError, match="must match"):
        replace(result, change_set=different_change_set)


def test_analysis_result_rejects_changed_file_id_outside_change_set_graph() -> None:
    result, _ = _analysis_fixture()
    build = result.review_unit_build_result
    assert build is not None
    forged_id = "changed-file:sha256:" + "0" * 64
    forged_files = [
        replace(file_result, changed_file_id=forged_id)
        for file_result in build.file_results
    ]
    forged_build = replace(build, file_results=forged_files)

    with pytest.raises(ValueError, match="do not match ChangeSet sources"):
        replace(result, review_unit_build_result=forged_build)


def test_analysis_result_rejects_unit_atom_outside_change_set_graph() -> None:
    result, _ = _analysis_fixture()
    build = result.review_unit_build_result
    assert build is not None
    forged_atom_id = "change-atom:sha256:" + "0" * 64
    forged_files: list[ReviewUnitFileResult] = []
    forged_units: list[ReviewUnit] = []
    for file_result in build.file_results:
        unit = replace(
            file_result.units[0],
            change_atom_ids=[forged_atom_id],
        )
        forged_units.append(unit)
        forged_files.append(replace(file_result, units=[unit]))
    forged_build = replace(build, file_results=forged_files)

    with pytest.raises(ValueError, match="change_atom_ids do not match"):
        replace(
            result,
            review_units=forged_units,
            review_unit_build_result=forged_build,
        )


def test_analysis_result_rejects_scope_source_drift_from_review_unit() -> None:
    result, _ = _analysis_fixture()
    forged_source_id = "code-source:sha256:" + "0" * 64
    forged_scope = replace(
        result.unit_fact_scopes[0],
        source_ref_id=forged_source_id,
    )
    forged_retrieval = replace(
        result.retrieval_query.units[0],
        source_ref_id=forged_source_id,
        unit_fact_scope=forged_scope,
    )
    query = replace(
        result.retrieval_query,
        units=[forged_retrieval, *result.retrieval_query.units[1:]],
    )

    with pytest.raises(ValueError, match="align by source_ref_id"):
        replace(
            result,
            retrieval_query=query,
            unit_fact_scopes=[forged_scope, *result.unit_fact_scopes[1:]],
        )


def test_analysis_result_rejects_fabricated_unit_exact_facts() -> None:
    result, _ = _analysis_fixture()
    forged_scope = replace(
        result.unit_fact_scopes[0],
        unit_exact=ScopedFacts(apis=("Fabricated.api",)),
    )
    forged_retrieval = replace(
        result.retrieval_query.units[0],
        code_features=CodeFeatures(apis=["Fabricated.api"]),
        unit_fact_scope=forged_scope,
    )
    query = replace(
        result.retrieval_query,
        units=[forged_retrieval, *result.retrieval_query.units[1:]],
    )

    with pytest.raises(ValueError, match="must equal occurrence projection"):
        replace(
            result,
            retrieval_query=query,
            unit_fact_scopes=[forged_scope, *result.unit_fact_scopes[1:]],
        )


def test_analysis_result_rejects_dangling_review_unit_owner() -> None:
    result, _ = _analysis_fixture()
    build = result.review_unit_build_result
    assert build is not None
    forged_unit = replace(
        result.review_units[0],
        owner_ref=OwnerRef("declaration", "declaration:missing"),
    )
    forged_file = replace(build.file_results[0], units=[forged_unit])
    forged_build = replace(
        build,
        file_results=[forged_file, *build.file_results[1:]],
    )

    with pytest.raises(ValueError, match="owner identity does not match"):
        replace(
            result,
            review_units=[forged_unit, *result.review_units[1:]],
            review_unit_build_result=forged_build,
        )


def test_analysis_result_rejects_valid_but_wrong_review_unit_owner() -> None:
    result, _ = _analysis_fixture()
    build = result.review_unit_build_result
    assert build is not None
    base_parse = result.file_parse_results[0]
    source_ref = base_parse.analysis.source_ref
    line_two_start = len(
        BASE_SOURCE.splitlines(keepends=True)[0].encode("utf-16-le")
    ) // 2
    other = DeclarationOccurrence.create(
        source_ref_id=source_ref.source_ref_id,
        kind="function",
        name="other",
        qualified_name="other",
        span=SourceSpan(2, 2),
        exact_range=ExactRange(
            2,
            2,
            line_two_start,
            line_two_start + len("  return 1"),
        ),
    )
    declarations = tuple(
        sorted(
            (*base_parse.analysis.declarations, other),
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
    forged_analysis = FileAnalysis.create(
        source_ref=source_ref,
        parser_version="fixture-parser-with-other-v1",
        parser_quality=base_parse.analysis.parser_quality,
        file_hints=base_parse.analysis.file_hints,
        declarations=declarations,
    )
    forged_parse = FileParseResult(
        analysis=forged_analysis,
        compatibility_facts=base_parse.compatibility_facts,
    )
    forged_unit = replace(
        result.review_units[0],
        owner_ref=OwnerRef("declaration", other.declaration_id),
    )
    forged_file = replace(build.file_results[0], units=[forged_unit])
    forged_build = replace(
        build,
        file_results=[forged_file, *build.file_results[1:]],
    )

    with pytest.raises(ValueError, match="owner identity does not match"):
        replace(
            result,
            review_units=[forged_unit, *result.review_units[1:]],
            review_unit_build_result=forged_build,
            file_parse_results=[forged_parse, *result.file_parse_results[1:]],
        )


def test_analysis_result_accepts_same_path_at_distinct_base_head_revisions() -> None:
    result, _ = _analysis_fixture()

    source_refs = [item.analysis.source_ref for item in result.file_parse_results]
    assert [source.path for source in source_refs] == [PATH, PATH]
    assert [source.revision for source in source_refs] == [
        BASE_REVISION,
        HEAD_REVISION,
    ]
    assert source_refs[0].source_ref_id != source_refs[1].source_ref_id
