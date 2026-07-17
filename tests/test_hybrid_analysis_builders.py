from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pytest

from arkts_code_reviewer.code_analysis import (
    AnalysisResult,
    ChangeAtomInput,
    ChangedFileInput,
    CodeAnalyzer,
    CodeSourceRef,
    CodeSourceSnapshot,
    ContextPlanResult,
    FactOccurrence,
    FileAnalysis,
    FileParseResult,
    RelationEdge,
    ReviewUnitSpan,
    normalize_change_set,
)
from arkts_code_reviewer.code_analysis.file_analysis_parser import (
    ArktsFileAnalysisParser,
)
from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder
from arkts_code_reviewer.hybrid_analysis import (
    AIModelViewProjectionPolicy,
    AnalysisCardBuilder,
    AnalysisContextPolicy,
    ReviewUnitAnalysisCard,
    build_ai_tag_model_view,
    build_review_unit_analysis_card,
    build_review_unit_analysis_cards,
    seal_ai_tag_model_view,
    seal_review_unit_analysis_card,
    verify_analysis_card_against_upstream,
    verify_model_view_against_card,
    verify_model_view_against_card_and_policy,
)

_REPOSITORY = "repo"
_BASE = "base"
_HEAD = "head"


class _ForgingFileParser:
    """Test-only parser that returns a self-consistent fact absent from source."""

    def __init__(self) -> None:
        self._delegate = ArktsFileAnalysisParser()

    def parse_file(
        self,
        source_ref: CodeSourceRef,
        source: str,
    ) -> FileParseResult:
        parsed = self._delegate.parse_file(source_ref, source)
        originals = tuple(
            item
            for item in parsed.analysis.fact_occurrences
            if item.kind == "call" and item.name == "console.info"
        )
        if not source_ref.path.endswith("Forged.ets") or not originals:
            return parsed
        original = originals[0]
        forged = FactOccurrence.create(
            source_ref_id=source_ref.source_ref_id,
            kind="api",
            name="http.createHttp",
            canonical_name="http.createHttp",
            span=original.span,
            exact_range=original.exact_range,
            owner_ref=original.owner_ref,
            quality=original.quality,
            provenance=original.provenance,
        )
        occurrences = tuple(
            sorted(
                (
                    forged if item.occurrence_id == original.occurrence_id else item
                    for item in parsed.analysis.fact_occurrences
                ),
                key=lambda item: (
                    item.span.start_line,
                    item.exact_range.start_offset_utf16,
                    item.span.end_line,
                    item.exact_range.end_offset_utf16,
                    item.kind,
                    item.canonical_name or item.name,
                    item.occurrence_id,
                ),
            )
        )
        analysis = FileAnalysis.create(
            source_ref=parsed.analysis.source_ref,
            parser_version=parsed.analysis.parser_version,
            parser_quality=parsed.analysis.parser_quality,
            file_hints=parsed.analysis.file_hints,
            declarations=parsed.analysis.declarations,
            review_regions=parsed.analysis.review_regions,
            fact_occurrences=occurrences,
            diagnostics=parsed.analysis.diagnostics,
        )
        return FileParseResult(
            analysis=analysis,
            compatibility_facts=parsed.compatibility_facts,
        )


@dataclass(frozen=True)
class _Scenario:
    analysis: AnalysisResult
    context_plan: ContextPlanResult
    snapshots: dict[str, CodeSourceSnapshot]


def _snapshot(path: str, content: str, revision: str) -> CodeSourceSnapshot:
    content_hash = f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"
    return CodeSourceSnapshot(
        source_ref=CodeSourceRef.create(
            repository=_REPOSITORY,
            revision=revision,
            path=path,
            content_hash=content_hash,
        ),
        content=content,
    )


def _replacement_scenario(
    base_text: str,
    head_text: str,
    *,
    changed_line: int,
    path: str = "src/Page.ets",
    analyzer: CodeAnalyzer | None = None,
) -> _Scenario:
    base = _snapshot(path, base_text, _BASE)
    head = _snapshot(path, head_text, _HEAD)
    change_set = normalize_change_set(
        repository=_REPOSITORY,
        base_revision=_BASE,
        head_revision=_HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path=path,
                new_path=path,
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="replacement",
                        old_span=ReviewUnitSpan(changed_line, changed_line),
                        new_span=ReviewUnitSpan(changed_line, changed_line),
                        deleted_old_lines=(changed_line,),
                        added_new_lines=(changed_line,),
                    ),
                ),
            ),
        ),
    )
    snapshots = {
        base.source_ref.source_ref_id: base,
        head.source_ref.source_ref_id: head,
    }
    analyzer = CodeAnalyzer() if analyzer is None else analyzer
    analysis = analyzer.analyze_change_set(change_set, snapshots)
    context_plan = analyzer.plan_context(
        analysis,
        source_snapshots=snapshots,
        code_context_budget=20_000,
    )
    return _Scenario(analysis, context_plan, snapshots)


def _method_scenario() -> _Scenario:
    return _replacement_scenario(
        """@Entry
@Component
struct Page {
  aboutToAppear() {
    console.info("old")
  }
}
""",
        """@Entry
@Component
struct Page {
  aboutToAppear() {
    console.info("new")
  }
}
""",
        changed_line=5,
    )


def _build_cards(scenario: _Scenario) -> tuple[ReviewUnitAnalysisCard, ...]:
    return build_review_unit_analysis_cards(
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=scenario.snapshots,
    )


def test_builder_replays_real_upstream_graph_and_is_deterministic() -> None:
    scenario = _method_scenario()
    cards = _build_cards(scenario)

    assert len(cards) == 2
    assert [card.source_role for card in cards] == ["base", "head"]
    assert all(card.unit_kind == "method" for card in cards)
    assert all(card.unit_symbol == "Page.aboutToAppear" for card in cards)
    assert all(card.code.mode == "full_unit" for card in cards)
    assert all(card.facts.unit_exact.symbols == ("Page.aboutToAppear",) for card in cards)
    assert all(card.quality.parser_layer == "L1" for card in cards)
    assert all(card.quality.error_nodes == 0 for card in cards)
    assert all(card.quality.missing_nodes == 0 for card in cards)

    rebuilt = _build_cards(scenario)
    assert rebuilt == cards
    for card in cards:
        verify_analysis_card_against_upstream(
            card,
            analysis_result=scenario.analysis,
            context_plan=scenario.context_plan,
            source_snapshots=scenario.snapshots,
        )


def test_batch_builder_replays_each_source_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _method_scenario()
    original = ArktsFileAnalysisParser.parse_file
    replayed_source_ids: list[str] = []

    def counting_parse(
        parser: ArktsFileAnalysisParser,
        source_ref: CodeSourceRef,
        source: str,
    ) -> FileParseResult:
        replayed_source_ids.append(source_ref.source_ref_id)
        return original(parser, source_ref, source)

    monkeypatch.setattr(ArktsFileAnalysisParser, "parse_file", counting_parse)
    cards = _build_cards(scenario)

    assert len(cards) == len(scenario.analysis.review_units)
    assert replayed_source_ids == [
        item.analysis.source_ref.source_ref_id
        for item in scenario.analysis.file_parse_results
    ]


def test_builder_preserves_occurrence_backed_owner_role_provenance() -> None:
    scenario = _method_scenario()
    head_card = next(card for card in _build_cards(scenario) if card.source_role == "head")

    assert head_card.owner_summary.resolution == "resolved"
    assert head_card.owner_summary.unit_owner is not None
    assert head_card.owner_summary.unit_owner.owner_kind == "method"
    assert head_card.owner_summary.enclosing_owner is not None
    assert head_card.owner_summary.enclosing_owner.owner_kind == "struct"
    assert head_card.owner_summary.owner_roles == (
        "arkui_custom_component",
        "arkui_router_page",
    )
    assert head_card.owner_context_occurrence_ids
    assert len(head_card.owner_context_declaration_ids) == 2
    assert not set(head_card.owner_context_occurrence_ids).intersection(
        head_card.exact_occurrence_ids
    )


def test_replacement_base_is_not_mislabeled_as_deletion() -> None:
    scenario = _method_scenario()
    base_card = next(card for card in _build_cards(scenario) if card.source_role == "base")

    assert base_card.code.mode == "full_unit"


def test_modified_file_pure_deletion_atom_uses_deletion_mode() -> None:
    base = _snapshot(
        "src/Modified.ets",
        "function retired() {\n  cleanup()\n}\nconst keep = 1\n",
        _BASE,
    )
    head = _snapshot("src/Modified.ets", "const keep = 1\n", _HEAD)
    change_set = normalize_change_set(
        repository=_REPOSITORY,
        base_revision=_BASE,
        head_revision=_HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path=base.source_ref.path,
                new_path=head.source_ref.path,
                old_snapshot=base,
                new_snapshot=head,
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
    snapshots = {
        base.source_ref.source_ref_id: base,
        head.source_ref.source_ref_id: head,
    }
    analyzer = CodeAnalyzer()
    analysis = analyzer.analyze_change_set(change_set, snapshots)
    plan = analyzer.plan_context(
        analysis,
        source_snapshots={base.source_ref.source_ref_id: base},
        code_context_budget=20_000,
    )

    assert len(analysis.review_units) == 1
    card = build_review_unit_analysis_card(
        analysis_result=analysis,
        context_plan=plan,
        source_snapshots=snapshots,
        unit_id=analysis.review_units[0].unit_id,
    )
    assert card.source_role == "base"
    assert card.code.mode == "deletion_base"


def test_mixed_deletion_and_replacement_atoms_do_not_use_deletion_mode() -> None:
    base = _snapshot(
        "src/Mixed.ets",
        'function changed() {\n  retired()\n  console.info("old")\n}\n',
        _BASE,
    )
    head = _snapshot(
        "src/Mixed.ets",
        'function changed() {\n  console.info("new")\n}\n',
        _HEAD,
    )
    change_set = normalize_change_set(
        repository=_REPOSITORY,
        base_revision=_BASE,
        head_revision=_HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path=base.source_ref.path,
                new_path=head.source_ref.path,
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="deletion",
                        old_span=ReviewUnitSpan(2, 2),
                        new_span=None,
                        deleted_old_lines=(2,),
                    ),
                    ChangeAtomInput(
                        kind="replacement",
                        old_span=ReviewUnitSpan(3, 3),
                        new_span=ReviewUnitSpan(2, 2),
                        deleted_old_lines=(3,),
                        added_new_lines=(2,),
                    ),
                ),
            ),
        ),
    )
    snapshots = {
        base.source_ref.source_ref_id: base,
        head.source_ref.source_ref_id: head,
    }
    analyzer = CodeAnalyzer()
    analysis = analyzer.analyze_change_set(change_set, snapshots)
    plan = analyzer.plan_context(
        analysis,
        source_snapshots=snapshots,
        code_context_budget=20_000,
    )
    base_unit = next(
        item for item in analysis.review_units if item.source_role == "base"
    )
    card = build_review_unit_analysis_card(
        analysis_result=analysis,
        context_plan=plan,
        source_snapshots=snapshots,
        unit_id=base_unit.unit_id,
    )

    assert len(card.change_atom_ids) == 2
    assert card.code.mode == "full_unit"


def test_deleted_file_uses_base_code_and_deletion_mode() -> None:
    source = "function retired() {\n  cleanup()\n}\n"
    base = _snapshot("src/Deleted.ets", source, _BASE)
    change_set = normalize_change_set(
        repository=_REPOSITORY,
        base_revision=_BASE,
        head_revision=_HEAD,
        files=(
            ChangedFileInput(
                status="deleted",
                old_path=base.source_ref.path,
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
    snapshots = {base.source_ref.source_ref_id: base}
    analyzer = CodeAnalyzer()
    analysis = analyzer.analyze_change_set(change_set, snapshots)
    plan = analyzer.plan_context(
        analysis,
        source_snapshots=snapshots,
        code_context_budget=20_000,
    )

    assert len(analysis.review_units) == 1
    card = build_review_unit_analysis_card(
        analysis_result=analysis,
        context_plan=plan,
        source_snapshots=snapshots,
        unit_id=analysis.review_units[0].unit_id,
    )
    assert card.source_role == "base"
    assert card.code.mode == "deletion_base"
    assert card.code.text == source.rstrip("\n")
    assert card.code.changed_line_numbers == (1, 2, 3)
    assert build_ai_tag_model_view(card=card).source_role == "base"


def test_fallback_card_keeps_degraded_context_without_fabricating_owner() -> None:
    source = "const value = doWork()\n"
    head = _snapshot("src/Fallback.ets", source, _HEAD)
    change_set = normalize_change_set(
        repository=_REPOSITORY,
        base_revision=_BASE,
        head_revision=_HEAD,
        files=(
            ChangedFileInput(
                status="added",
                old_path=None,
                new_path=head.source_ref.path,
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
    snapshots = {head.source_ref.source_ref_id: head}
    analyzer = CodeAnalyzer()
    analysis = analyzer.analyze_change_set(change_set, snapshots)
    plan = analyzer.plan_context(
        analysis,
        source_snapshots=snapshots,
        code_context_budget=20_000,
    )

    card = build_review_unit_analysis_card(
        analysis_result=analysis,
        context_plan=plan,
        source_snapshots=snapshots,
        unit_id=analysis.review_units[0].unit_id,
    )
    assert card.unit_kind == "fallback"
    assert card.quality.context_degraded is True
    assert card.quality.unit_owner_unresolved is False
    assert card.owner_summary.resolution == "not_applicable"
    assert card.owner_summary.unit_owner is None
    assert card.exact_occurrence_ids == ()
    assert card.facts.unit_exact.symbols == ()


def test_single_blank_changed_line_is_a_valid_card_and_numbered_model_view() -> None:
    source = "\n"
    head = _snapshot("src/Blank.ets", source, _HEAD)
    change_set = normalize_change_set(
        repository=_REPOSITORY,
        base_revision=_BASE,
        head_revision=_HEAD,
        files=(
            ChangedFileInput(
                status="added",
                old_path=None,
                new_path=head.source_ref.path,
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
    snapshots = {head.source_ref.source_ref_id: head}
    analyzer = CodeAnalyzer()
    analysis = analyzer.analyze_change_set(change_set, snapshots)
    plan = analyzer.plan_context(
        analysis,
        source_snapshots=snapshots,
        code_context_budget=20_000,
    )
    card = build_review_unit_analysis_card(
        analysis_result=analysis,
        context_plan=plan,
        source_snapshots=snapshots,
        unit_id=analysis.review_units[0].unit_id,
    )
    view = build_ai_tag_model_view(card=card)

    assert card.code.text == ""
    assert card.code.line_start == card.code.line_end == 1
    assert view.source_role == "head"
    assert view.code.numbered_text == "1: "
    assert view.code.line_numbers == (1,)


def test_crlf_and_astral_unicode_keep_utf16_ranges_and_physical_lines_aligned() -> None:
    scenario = _replacement_scenario(
        'function changed() {\r\n  const icon = "😀"\r\n  console.info("old")\r\n}\r\n',
        'function changed() {\r\n  const icon = "😀"\r\n  console.info("new")\r\n}\r\n',
        changed_line=3,
        path="src/Unicode.ets",
    )
    card = next(card for card in _build_cards(scenario) if card.source_role == "head")
    view = build_ai_tag_model_view(card=card)

    assert card.code.text == (
        'function changed() {\n  const icon = "😀"\n  console.info("new")\n}'
    )
    assert view.code.line_numbers == (1, 2, 3, 4)
    assert view.code.numbered_text.split("\n")[1] == '2:   const icon = "😀"'


def test_active_static_tags_keep_exact_and_file_hint_scopes_separate() -> None:
    scenario = _replacement_scenario(
        """struct Page {
  build() {
    Text("old")
  }
}
""",
        """struct Page {
  build() {
    Text("new")
  }
}
""",
        changed_line=3,
    )
    head_card = next(card for card in _build_cards(scenario) if card.source_role == "head")

    assert head_card.static_tags.exact == ("has_text_display",)
    assert head_card.static_tags.routing == ("has_text_display",)
    assert tuple((item.scope, item.tag_id) for item in head_card.static_tags.matches) == (
        ("file_hint", "has_text_display"),
        ("unit_exact", "has_text_display"),
    )
    assert head_card.facts.unit_exact.components == ("Text",)
    assert head_card.facts.file_hints.components == ("Text",)


def test_context_refs_only_expose_outgoing_exact_primary_targets() -> None:
    scenario = _method_scenario()
    base_card = next(card for card in _build_cards(scenario) if card.source_role == "base")
    head_card = next(card for card in _build_cards(scenario) if card.source_role == "head")

    assert len(base_card.available_context_refs) == 1
    assert base_card.available_context_refs[0].relation_type == "change_correspondence"
    assert base_card.available_context_refs[0].target_unit_id == head_card.unit_id
    assert head_card.available_context_refs == ()


def test_unverified_primary_relation_is_not_exposed_as_available_context() -> None:
    scenario = _method_scenario()
    base_unit = next(
        item for item in scenario.analysis.review_units if item.source_role == "base"
    )
    head_unit = next(
        item for item in scenario.analysis.review_units if item.source_role == "head"
    )
    fake = RelationEdge.create(
        source_ref=head_unit.unit_id,
        target_ref=base_unit.unit_id,
        relation_type="direct_call",
        strength="weak",
        quality="exact",
        evidence_refs=("caller-declared:evidence",),
        provenance_ref="caller-declared:provenance",
    )
    original = scenario.context_plan
    plan = ContextPlanResult.create(
        change_set_id=original.change_set_id,
        blocking_change_ids=original.blocking_change_ids,
        primary_question_bindings=original.primary_question_bindings,
        candidates=original.candidates,
        supporting_segments=original.supporting_segments,
        relation_edges=tuple(sorted((*original.relation_edges, fake), key=lambda x: x.edge_id)),
        change_groups=original.change_groups,
        bundles=original.bundles,
        omitted_candidates=original.omitted_candidates,
        budget_summary=original.budget_summary,
        diagnostics=original.diagnostics,
    )

    head_card = build_review_unit_analysis_card(
        analysis_result=scenario.analysis,
        context_plan=plan,
        source_snapshots=scenario.snapshots,
        unit_id=head_unit.unit_id,
    )
    assert head_card.available_context_refs == ()


def test_large_unit_uses_one_contiguous_window_that_keeps_all_changed_lines() -> None:
    base_lines = ["function changed() {"]
    base_lines.extend(f"  const value{index} = {index}" for index in range(1, 21))
    base_lines.append("}")
    head_lines = list(base_lines)
    head_lines[10] = "  const value10 = 1000"
    scenario = _replacement_scenario(
        "\n".join(base_lines) + "\n",
        "\n".join(head_lines) + "\n",
        changed_line=11,
        path="src/Long.ets",
    )
    policy = AnalysisContextPolicy(
        code_token_budget=200,
        max_full_unit_lines=5,
        max_full_unit_characters=100,
        changed_window_context_lines=2,
    )
    head_unit = next(
        item for item in scenario.analysis.review_units if item.source_role == "head"
    )

    card = build_review_unit_analysis_card(
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=scenario.snapshots,
        unit_id=head_unit.unit_id,
        policy=policy,
    )
    assert card.code.mode == "changed_window"
    assert card.code.truncated is True
    assert card.code.line_start == 10
    assert card.code.line_end == 12
    assert card.code.changed_line_numbers == (11,)
    assert "value10 = 1000" in card.code.text


def test_changed_window_cannot_expand_back_past_full_unit_line_limit() -> None:
    scenario = _method_scenario()
    head_unit = next(
        item for item in scenario.analysis.review_units if item.source_role == "head"
    )
    policy = AnalysisContextPolicy(
        code_token_budget=200,
        max_full_unit_lines=1,
        max_full_unit_characters=200,
        changed_window_context_lines=6,
    )

    card = build_review_unit_analysis_card(
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=scenario.snapshots,
        unit_id=head_unit.unit_id,
        policy=policy,
    )
    assert card.code.mode == "changed_window"
    assert card.code.line_start == card.code.line_end == 5
    assert card.code.changed_line_numbers == (5,)


def test_minimum_changed_window_fails_closed_when_it_exceeds_budget() -> None:
    scenario = _method_scenario()
    head_unit = next(
        item for item in scenario.analysis.review_units if item.source_role == "head"
    )
    policy = AnalysisContextPolicy(
        code_token_budget=1,
        max_full_unit_lines=1,
        max_full_unit_characters=1,
        changed_window_context_lines=0,
    )

    with pytest.raises(ValueError, match="minimum continuous changed-line window"):
        build_review_unit_analysis_card(
            analysis_result=scenario.analysis,
            context_plan=scenario.context_plan,
            source_snapshots=scenario.snapshots,
            unit_id=head_unit.unit_id,
            policy=policy,
        )


def test_minimum_changed_line_fails_closed_when_it_exceeds_character_cap() -> None:
    scenario = _method_scenario()
    head_unit = next(
        item for item in scenario.analysis.review_units if item.source_role == "head"
    )
    policy = AnalysisContextPolicy(
        code_token_budget=200,
        max_full_unit_lines=10,
        max_full_unit_characters=5,
        changed_window_context_lines=0,
    )

    with pytest.raises(ValueError, match="minimum continuous changed-line window"):
        build_review_unit_analysis_card(
            analysis_result=scenario.analysis,
            context_plan=scenario.context_plan,
            source_snapshots=scenario.snapshots,
            unit_id=head_unit.unit_id,
            policy=policy,
        )


def test_builder_rejects_review_unit_text_not_backed_by_snapshot() -> None:
    scenario = _method_scenario()
    unit = scenario.analysis.review_units[0]
    unit.full_text = "aboutToAppear() {\n  forged()\n}"

    with pytest.raises(ValueError, match="canonical ReviewUnit Builder replay"):
        build_review_unit_analysis_card(
            analysis_result=scenario.analysis,
            context_plan=scenario.context_plan,
            source_snapshots=scenario.snapshots,
            unit_id=unit.unit_id,
        )


def test_builder_rejects_self_consistent_parser_facts_absent_from_source() -> None:
    scenario = _replacement_scenario(
        """function changed() {
  console.info("old")
}
""",
        """function changed() {
  console.info("new")
}
""",
        changed_line=2,
        path="src/Forged.ets",
        analyzer=CodeAnalyzer(file_parser=_ForgingFileParser()),
    )
    head_profile = next(
        item
        for item in scenario.analysis.feature_routing_result.units
        if item.unit_id
        == next(
            unit.unit_id
            for unit in scenario.analysis.review_units
            if unit.source_role == "head"
        )
    )
    assert "has_network" in head_profile.exact_tags
    head_unit = next(
        item for item in scenario.analysis.review_units if item.source_role == "head"
    )

    with pytest.raises(ValueError, match="trusted Parser replay"):
        build_review_unit_analysis_card(
            analysis_result=scenario.analysis,
            context_plan=scenario.context_plan,
            source_snapshots=scenario.snapshots,
            unit_id=head_unit.unit_id,
        )


def test_builder_replays_every_source_before_binding_global_routing_identity() -> None:
    safe_base = _snapshot(
        "src/Safe.ets",
        'function safe() {\n  console.info("old")\n}\n',
        _BASE,
    )
    safe_head = _snapshot(
        "src/Safe.ets",
        'function safe() {\n  console.info("new")\n}\n',
        _HEAD,
    )
    forged_base = _snapshot(
        "src/Forged.ets",
        'function forged() {\n  console.info("old")\n}\n',
        _BASE,
    )
    forged_head = _snapshot(
        "src/Forged.ets",
        'function forged() {\n  console.info("new")\n}\n',
        _HEAD,
    )
    change_set = normalize_change_set(
        repository=_REPOSITORY,
        base_revision=_BASE,
        head_revision=_HEAD,
        files=tuple(
            ChangedFileInput(
                status="modified",
                old_path=base.source_ref.path,
                new_path=head.source_ref.path,
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
            )
            for base, head in (
                (safe_base, safe_head),
                (forged_base, forged_head),
            )
        ),
    )
    snapshots = {
        item.source_ref.source_ref_id: item
        for item in (safe_base, safe_head, forged_base, forged_head)
    }
    analyzer = CodeAnalyzer(file_parser=_ForgingFileParser())
    analysis = analyzer.analyze_change_set(change_set, snapshots)
    plan = analyzer.plan_context(
        analysis,
        source_snapshots=snapshots,
        code_context_budget=20_000,
    )
    safe_head_unit = next(
        item
        for item in analysis.review_units
        if item.source_role == "head" and item.file == "src/Safe.ets"
    )

    with pytest.raises(ValueError, match="trusted Parser replay"):
        build_review_unit_analysis_card(
            analysis_result=analysis,
            context_plan=plan,
            source_snapshots=snapshots,
            unit_id=safe_head_unit.unit_id,
        )


def test_production_builder_does_not_allow_parser_trust_root_injection() -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'file_parser'"):
        AnalysisCardBuilder(file_parser=_ForgingFileParser())  # type: ignore[call-arg]


def test_builder_rejects_non_fallback_context_span_expansion() -> None:
    scenario = _method_scenario()
    unit = next(
        item for item in scenario.analysis.review_units if item.source_role == "head"
    )
    snapshot = scenario.snapshots[unit.source_ref_id]
    unit.context_span = ReviewUnitSpan(1, len(snapshot.content.splitlines()))
    unit.full_text = snapshot.content.rstrip("\n")
    unit.unit_changed_lines = list(unit.changed_new_lines)
    scenario.analysis.validate()

    with pytest.raises(ValueError, match="canonical ReviewUnit Builder replay"):
        build_review_unit_analysis_card(
            analysis_result=scenario.analysis,
            context_plan=scenario.context_plan,
            source_snapshots=scenario.snapshots,
            unit_id=unit.unit_id,
        )


def test_builder_rejects_noncanonical_fallback_context_policy() -> None:
    base_lines = [f"const value{line} = {line}" for line in range(1, 101)]
    head_lines = list(base_lines)
    head_lines[49] = "const value50 = 5000"
    scenario = _replacement_scenario(
        "\n".join(base_lines) + "\n",
        "\n".join(head_lines) + "\n",
        changed_line=50,
        path="src/ExpandedFallback.ets",
        analyzer=CodeAnalyzer(unit_builder=ReviewUnitBuilder(fallback_context_lines=100)),
    )
    unit = next(
        item for item in scenario.analysis.review_units if item.source_role == "head"
    )
    assert unit.context_span == ReviewUnitSpan(1, 100)

    with pytest.raises(ValueError, match="canonical ReviewUnit Builder replay"):
        build_review_unit_analysis_card(
            analysis_result=scenario.analysis,
            context_plan=scenario.context_plan,
            source_snapshots=scenario.snapshots,
            unit_id=unit.unit_id,
        )


def test_builder_requires_exact_changeset_snapshot_coverage() -> None:
    scenario = _method_scenario()
    unit = scenario.analysis.review_units[0]
    missing = dict(scenario.snapshots)
    missing.pop(next(iter(missing)))

    with pytest.raises(ValueError, match="exactly cover ChangeSet.source_refs"):
        build_review_unit_analysis_card(
            analysis_result=scenario.analysis,
            context_plan=scenario.context_plan,
            source_snapshots=missing,
            unit_id=unit.unit_id,
        )


def test_upstream_verifier_rejects_a_self_consistent_forged_card() -> None:
    scenario = _method_scenario()
    card = _build_cards(scenario)[0]
    payload = card.model_dump(mode="json", exclude={"card_id"})
    code = dict(payload["code"])
    code["text"] = code["text"].replace("old", "bad")
    payload["code"] = code
    forged = seal_review_unit_analysis_card(payload)

    assert forged.card_id != card.card_id
    with pytest.raises(ValueError, match="does not match the supplied upstream graph"):
        verify_analysis_card_against_upstream(
            forged,
            analysis_result=scenario.analysis,
            context_plan=scenario.context_plan,
            source_snapshots=scenario.snapshots,
        )


def test_model_view_is_an_exact_whitelist_projection_without_static_labels() -> None:
    scenario = _replacement_scenario(
        """struct Page {
  build() {
    Text("old")
  }
}
""",
        """struct Page {
  build() {
    Text("new")
  }
}
""",
        changed_line=3,
    )
    card = next(card for card in _build_cards(scenario) if card.source_role == "head")
    view = build_ai_tag_model_view(card=card)

    verify_model_view_against_card(view, card)
    verify_model_view_against_card_and_policy(view, card)
    assert set(type(view).model_fields) == {
        "schema_version",
        "model_view_id",
        "card_id",
        "unit_id",
        "source_ref_id",
        "source_role",
        "code",
        "owner_summary",
        "scoped_facts",
        "quality",
        "projection_policy_fingerprint",
    }
    serialized = view.model_dump_json()
    for forbidden in (
        "static_tags",
        "has_text_display",
        "feature_profile_id",
        "feature_routing_id",
        "context_plan_id",
        "available_context_refs",
        "exact_occurrence_ids",
        "owner_context_declaration_ids",
        "dimension",
        "review_question",
    ):
        assert forbidden not in serialized
    assert view.code.numbered_text == "2:   build() {\n3:     Text(\"new\")\n4:   }"


def test_model_view_v2_binds_source_role_and_rejects_v1_wire_payload() -> None:
    scenario = _method_scenario()
    card = next(card for card in _build_cards(scenario) if card.source_role == "head")
    view = build_ai_tag_model_view(card=card)
    payload = view.model_dump(mode="json", exclude={"model_view_id"})
    payload["source_role"] = "base"
    wrong_role = seal_ai_tag_model_view(payload)

    with pytest.raises(ValueError, match="does not reference the supplied Analysis Card"):
        verify_model_view_against_card(wrong_role, card)

    payload["source_role"] = "head"
    payload["schema_version"] = "ai-tag-model-view-v1"
    with pytest.raises(ValueError, match="schema_version"):
        seal_ai_tag_model_view(payload)


def test_all_model_view_verifiers_reject_an_opaque_fingerprint_swap() -> None:
    scenario = _method_scenario()
    card = _build_cards(scenario)[0]
    view = build_ai_tag_model_view(card=card)
    payload = view.model_dump(mode="json", exclude={"model_view_id"})
    payload["projection_policy_fingerprint"] = (
        "ai-model-view-policy:sha256:" + "0" * 64
    )
    forged = seal_ai_tag_model_view(payload)

    with pytest.raises(ValueError, match="unsupported projection policy"):
        verify_model_view_against_card(forged, card)
    with pytest.raises(ValueError, match="unsupported projection policy"):
        verify_model_view_against_card_and_policy(forged, card)


def test_policy_fingerprints_change_artifact_identity() -> None:
    scenario = _method_scenario()
    head_unit = next(
        item for item in scenario.analysis.review_units if item.source_role == "head"
    )
    default_card = build_review_unit_analysis_card(
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=scenario.snapshots,
        unit_id=head_unit.unit_id,
    )
    changed_policy = AnalysisContextPolicy(changed_window_context_lines=7)
    changed_card = build_review_unit_analysis_card(
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=scenario.snapshots,
        unit_id=head_unit.unit_id,
        policy=changed_policy,
    )
    assert changed_card.context_policy_fingerprint == changed_policy.fingerprint
    assert changed_card.card_id != default_card.card_id
    with pytest.raises(ValueError, match="does not match the supplied upstream graph"):
        verify_analysis_card_against_upstream(
            default_card,
            analysis_result=scenario.analysis,
            context_plan=scenario.context_plan,
            source_snapshots=scenario.snapshots,
            policy=changed_policy,
        )

    default_view = build_ai_tag_model_view(card=default_card)
    projection_policy = AIModelViewProjectionPolicy()
    same_view = build_ai_tag_model_view(card=default_card, policy=projection_policy)
    assert same_view == default_view
