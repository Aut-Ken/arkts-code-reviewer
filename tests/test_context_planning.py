from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from arkts_code_reviewer.code_analysis.change_set import CodeSourceSnapshot
from arkts_code_reviewer.code_analysis.context_planning import (
    CONTEXT_PLAN_SCHEMA_VERSION,
    TOKEN_ESTIMATOR_VERSION,
    BundleBudget,
    CandidateOmission,
    ChangeGroup,
    ContextCandidate,
    ContextDiagnostic,
    ContextPlanner,
    ContextPlanResult,
    QuestionBinding,
    RelationEdge,
    ReviewContextBundle,
    estimate_code_tokens,
    source_span_ref_id,
)
from arkts_code_reviewer.code_analysis.file_analysis_models import (
    CodeSourceRef,
    ExactRange,
)
from arkts_code_reviewer.code_analysis.models import (
    HostSummary,
    ReviewUnit,
    ReviewUnitDiagnostic,
    ReviewUnitSpan,
)
from arkts_code_reviewer.code_analysis.review_unit_contract import fallback_unit_id
from arkts_code_reviewer.code_analysis.text_utils import extract_lines


def _snapshot(path: str, content: str, *, revision: str = "head") -> CodeSourceSnapshot:
    inline = CodeSourceRef.inline(path, content, repository="context-test")
    source_ref = CodeSourceRef.create(
        repository=inline.repository,
        revision=revision,
        path=inline.path,
        content_hash=inline.content_hash,
    )
    return CodeSourceSnapshot(source_ref, content)


def _line_range(content: str, start_line: int, end_line: int) -> ExactRange:
    lines = content.splitlines(keepends=True)
    start = sum(len(line.encode("utf-16-le")) // 2 for line in lines[: start_line - 1])
    end = start + sum(
        len(line.encode("utf-16-le")) // 2
        for line in lines[start_line - 1 : end_line]
    )
    return ExactRange(start_line, end_line, start, end)


def _primary(
    snapshot: CodeSourceSnapshot,
    line: int,
    *,
    atom_suffix: str = "1",
    role: str = "head",
) -> ReviewUnit:
    path = snapshot.source_ref.path
    span = ReviewUnitSpan(line, line)
    symbol = f"hunk-L{line}-L{line}"
    changed_new_lines = [line] if role == "head" else []
    changed_old_lines = [line] if role == "base" else []
    return ReviewUnit(
        file=path,
        unit_symbol=symbol,
        unit_ref=f"{symbol}@{path}",
        full_text=extract_lines(snapshot.content, line, line),
        changed_lines=[line],
        file_changed_lines=[line],
        unit_changed_lines=[1],
        host_summary=HostSummary(),
        context_degraded=True,
        unit_id=fallback_unit_id(
            path,
            line,
            line,
            line,
            line,
            source_role=role,  # type: ignore[arg-type]
            source_ref_id=snapshot.source_ref.source_ref_id,
        ),
        unit_kind="fallback",
        source_span=span,
        context_span=span,
        changed_new_lines=changed_new_lines,
        selection_reason="fallback_window",
        diagnostics=[ReviewUnitDiagnostic("no_matching_declaration")],
        source_ref_id=snapshot.source_ref.source_ref_id,
        source_role=role,  # type: ignore[arg-type]
        change_atom_ids=[f"change-atom:sha256:{atom_suffix.zfill(64)}"],
        changed_old_lines=changed_old_lines,
        identity_source_ref_id=snapshot.source_ref.source_ref_id,
    )


def _candidate(
    *,
    primary: ReviewUnit,
    snapshot: CodeSourceSnapshot,
    span: ExactRange,
    question: str = "correctness",
    necessity: str = "required",
    relation_type: str = "direct_call",
    quality: str = "exact",
    strength: str = "strong",
    provenance: str = "fixture:relation:1",
) -> tuple[ContextCandidate, RelationEdge]:
    target_ref = source_span_ref_id(snapshot.source_ref.source_ref_id, span)
    boundary_ref = (
        "declaration:sha256:"
        + hashlib.sha256(target_ref.encode("utf-8")).hexdigest()
    )
    edge = RelationEdge.create(
        source_ref=primary.unit_id,
        target_ref=target_ref,
        relation_type=relation_type,  # type: ignore[arg-type]
        strength=strength,  # type: ignore[arg-type]
        quality=quality,  # type: ignore[arg-type]
        evidence_refs=(boundary_ref, f"fixture:evidence:{span.start_line}"),
        provenance_ref=provenance,
    )
    source_text = _exact_text(snapshot.content, span)
    candidate = ContextCandidate.create(
        primary_unit_id=primary.unit_id,
        review_question_id=question,
        relation_edge_id=edge.edge_id,
        relation_type=relation_type,  # type: ignore[arg-type]
        target_source_ref_id=snapshot.source_ref.source_ref_id,
        target_span=span,
        estimated_tokens=estimate_code_tokens(source_text),
        necessity=necessity,  # type: ignore[arg-type]
        provenance_ref=boundary_ref,
    )
    return candidate, edge


def _exact_text(source: str, span: ExactRange) -> str:
    boundaries = {0: 0}
    offset = 0
    for index, character in enumerate(source, start=1):
        offset += 2 if ord(character) > 0xFFFF else 1
        boundaries[offset] = index
    return source[
        boundaries[span.start_offset_utf16] : boundaries[span.end_offset_utf16]
    ]


def _plan(
    *,
    primaries: tuple[ReviewUnit, ...],
    snapshots: tuple[CodeSourceSnapshot, ...],
    candidates: tuple[ContextCandidate, ...] = (),
    edges: tuple[RelationEdge, ...] = (),
    budget: int = 1000,
    questions: tuple[str, ...] = ("correctness",),
) -> ContextPlanResult:
    return ContextPlanner().plan(
        change_set_id=f"change-set:sha256:{'c' * 64}",
        primary_units=primaries,
        primary_question_bindings=tuple(
            QuestionBinding(primary.unit_id, question)
            for primary in primaries
            for question in questions
        ),
        source_snapshots=snapshots,
        candidates=candidates,
        relation_edges=edges,
        code_context_budget=budget,
    )


def test_plan_keeps_every_primary_and_serializes_stably() -> None:
    source = _snapshot("src/Main.ets", "function changed() {}\n")
    primary = _primary(source, 1)

    result = _plan(primaries=(primary,), snapshots=(source,))

    assert result.schema_version == CONTEXT_PLAN_SCHEMA_VERSION
    assert result.token_estimator_version == TOKEN_ESTIMATOR_VERSION
    assert result.change_groups[0].primary_unit_ids == (primary.unit_id,)
    assert result.bundles[0].primary_unit_ids == (primary.unit_id,)
    assert result.bundles[0].dispatch_allowed is True
    assert result.budget_summary.total_primary_tokens == estimate_code_tokens(
        primary.full_text
    )
    assert result.to_dict()["context_plan_id"] == result.context_plan_id


def test_only_typed_strong_exact_primary_edges_form_groups() -> None:
    first_source = _snapshot("src/A.ets", "function a() {}\n")
    second_source = _snapshot("src/B.ets", "function b() {}\n")
    first = _primary(first_source, 1, atom_suffix="1")
    second = _primary(second_source, 1, atom_suffix="2")
    strong = RelationEdge.create(
        source_ref=first.unit_id,
        target_ref=second.unit_id,
        relation_type="direct_call",
        strength="strong",
        quality="exact",
        evidence_refs=("fixture:call",),
        provenance_ref="fixture:primary-edge",
    )

    grouped = _plan(
        primaries=(second, first),
        snapshots=(second_source, first_source),
        edges=(strong,),
    )
    assert len(grouped.change_groups) == 1
    assert set(grouped.change_groups[0].primary_unit_ids) == {
        first.unit_id,
        second.unit_id,
    }

    same_file = RelationEdge.create(
        source_ref=first.unit_id,
        target_ref=second.unit_id,
        relation_type="same_file",
        strength="strong",
        quality="exact",
        evidence_refs=("fixture:path",),
        provenance_ref="fixture:same-file",
    )
    ungrouped = _plan(
        primaries=(first, second),
        snapshots=(first_source, second_source),
        edges=(same_file,),
    )
    assert len(ungrouped.change_groups) == 2


def test_shared_change_atom_automatically_groups_base_and_head_primaries() -> None:
    base_source = _snapshot(
        "src/A.ets",
        "function changed() { return 1 }\n",
        revision="base",
    )
    head_source = _snapshot(
        "src/A.ets",
        "function changed() { return 2 }\n",
        revision="head",
    )
    base = _primary(base_source, 1, atom_suffix="7", role="base")
    head = _primary(head_source, 1, atom_suffix="7", role="head")

    result = _plan(
        primaries=(head, base),
        snapshots=(head_source, base_source),
    )

    assert len(result.change_groups) == 1
    assert result.change_groups[0].primary_unit_ids == tuple(
        sorted((base.unit_id, head.unit_id))
    )
    correspondence = [
        edge
        for edge in result.relation_edges
        if edge.relation_type == "change_correspondence"
    ]
    assert len(correspondence) == 1
    assert correspondence[0].evidence_refs == (base.change_atom_ids[0],)
    assert all(
        set(bundle.primary_unit_ids) == {base.unit_id, head.unit_id}
        for bundle in result.bundles
    )


def test_change_correspondence_cannot_be_injected_or_used_for_supporting() -> None:
    first_source = _snapshot("src/A.ets", "function a() {}\n")
    second_source = _snapshot("src/B.ets", "function b() {}\n")
    first = _primary(first_source, 1, atom_suffix="1")
    second = _primary(second_source, 1, atom_suffix="2")
    forged_edge = RelationEdge.create(
        source_ref=first.unit_id,
        target_ref=second.unit_id,
        relation_type="change_correspondence",
        strength="strong",
        quality="exact",
        evidence_refs=(f"change-atom:sha256:{'f' * 64}",),
        provenance_ref=f"change-set:sha256:{'c' * 64}",
    )

    with pytest.raises(ValueError, match="planner-derived only"):
        _plan(
            primaries=(first, second),
            snapshots=(first_source, second_source),
            edges=(forged_edge,),
        )

    span = _line_range(second_source.content, 1, 1)
    boundary_ref = f"declaration:sha256:{'e' * 64}"
    supporting_edge = RelationEdge.create(
        source_ref=first.unit_id,
        target_ref=source_span_ref_id(second_source.source_ref.source_ref_id, span),
        relation_type="change_correspondence",
        strength="strong",
        quality="exact",
        evidence_refs=(boundary_ref,),
        provenance_ref=f"change-set:sha256:{'c' * 64}",
    )
    with pytest.raises(ValueError, match="planner-derived change_correspondence"):
        ContextCandidate.create(
            primary_unit_id=first.unit_id,
            review_question_id="correctness",
            relation_edge_id=supporting_edge.edge_id,
            relation_type="change_correspondence",
            target_source_ref_id=second_source.source_ref.source_ref_id,
            target_span=span,
            estimated_tokens=estimate_code_tokens(second_source.content),
            necessity="required",
            provenance_ref=boundary_ref,
        )


def test_required_precedes_helpful_and_helpful_budget_omission_is_safe() -> None:
    source = _snapshot(
        "src/Main.ets",
        "function changed() {}\nfunction required() {}\nfunction helpful() {}\n",
    )
    primary = _primary(source, 1)
    required, required_edge = _candidate(
        primary=primary,
        snapshot=source,
        span=_line_range(source.content, 2, 2),
        necessity="required",
        provenance="fixture:required",
    )
    helpful, helpful_edge = _candidate(
        primary=primary,
        snapshot=source,
        span=_line_range(source.content, 3, 3),
        necessity="helpful",
        provenance="fixture:helpful",
    )
    limit = estimate_code_tokens(primary.full_text) + required.estimated_tokens

    result = _plan(
        primaries=(primary,),
        snapshots=(source,),
        candidates=(helpful, required),
        edges=(helpful_edge, required_edge),
        budget=limit,
    )

    assert [item.candidate_id for item in result.supporting_segments] == [
        required.candidate_id
    ]
    assert result.omitted_candidate_ids == (helpful.candidate_id,)
    assert result.omitted_candidates[0].reason == "budget_exceeded"
    assert result.bundles[0].dispatch_allowed is True
    assert result.bundles[0].budget.total_tokens == limit


def test_helpful_context_splits_across_bundles_and_repeats_required() -> None:
    source = _snapshot(
        "src/Main.ets",
        "function changed() {}\n"
        "function required() {}\n"
        "function helperOne() {}\n"
        "function helperTwo() {}\n",
    )
    primary = _primary(source, 1)
    required, required_edge = _candidate(
        primary=primary,
        snapshot=source,
        span=_line_range(source.content, 2, 2),
        necessity="required",
        provenance="fixture:required",
    )
    first_helpful, first_edge = _candidate(
        primary=primary,
        snapshot=source,
        span=_line_range(source.content, 3, 3),
        necessity="helpful",
        provenance="fixture:helpful-one",
    )
    second_helpful, second_edge = _candidate(
        primary=primary,
        snapshot=source,
        span=_line_range(source.content, 4, 4),
        necessity="helpful",
        provenance="fixture:helpful-two",
    )
    limit = (
        estimate_code_tokens(primary.full_text)
        + required.estimated_tokens
        + max(first_helpful.estimated_tokens, second_helpful.estimated_tokens)
    )

    result = _plan(
        primaries=(primary,),
        snapshots=(source,),
        candidates=(second_helpful, required, first_helpful),
        edges=(second_edge, required_edge, first_edge),
        budget=limit,
    )

    assert len(result.bundles) == 2
    assert all(bundle.dispatch_allowed for bundle in result.bundles)
    required_segment = next(
        segment
        for segment in result.supporting_segments
        if segment.candidate_id == required.candidate_id
    )
    assert all(
        required_segment.segment_id in bundle.supporting_segment_ids
        for bundle in result.bundles
    )
    helpful_segment_ids = {
        segment.segment_id
        for segment in result.supporting_segments
        if segment.candidate_id
        in {first_helpful.candidate_id, second_helpful.candidate_id}
    }
    assert {
        segment_id
        for bundle in result.bundles
        for segment_id in bundle.supporting_segment_ids
        if segment_id in helpful_segment_ids
    } == helpful_segment_ids
    assert result.omitted_candidates == ()
    assert result.budget_summary.total_supporting_tokens == (
        required.estimated_tokens * 2
        + first_helpful.estimated_tokens
        + second_helpful.estimated_tokens
    )


def test_each_question_gets_a_separate_bundle_with_all_primary_code() -> None:
    source = _snapshot("src/Main.ets", "function changed() {}\n")
    primary = _primary(source, 1)

    result = _plan(
        primaries=(primary,),
        snapshots=(source,),
        questions=("correctness", "impact"),
    )

    assert len(result.bundles) == 2
    assert all(bundle.primary_unit_ids == (primary.unit_id,) for bundle in result.bundles)
    assert {
        bundle.primary_question_bindings[0].review_question_id
        for bundle in result.bundles
    } == {"correctness", "impact"}


def test_required_budget_omission_blocks_dispatch_without_dropping_primary() -> None:
    source = _snapshot(
        "src/Main.ets",
        "function changed() {}\nfunction requiredLongName() { return 123456; }\n",
    )
    primary = _primary(source, 1)
    candidate, edge = _candidate(
        primary=primary,
        snapshot=source,
        span=_line_range(source.content, 2, 2),
    )
    result = _plan(
        primaries=(primary,),
        snapshots=(source,),
        candidates=(candidate,),
        edges=(edge,),
        budget=estimate_code_tokens(primary.full_text),
    )

    assert result.supporting_segments == ()
    assert result.omitted_candidate_ids == (candidate.candidate_id,)
    assert result.bundles[0].primary_unit_ids == (primary.unit_id,)
    assert result.bundles[0].dispatch_allowed is False
    assert {item.code for item in result.bundles[0].diagnostics} == {
        "context_insufficient"
    }


def test_primary_overflow_is_visible_and_never_truncated() -> None:
    source = _snapshot(
        "src/Main.ets",
        "function changedWithAReallyLongName() { return 'large payload'; }\n",
    )
    primary = _primary(source, 1)

    result = _plan(primaries=(primary,), snapshots=(source,), budget=1)

    assert result.bundles[0].primary_unit_ids == (primary.unit_id,)
    assert result.bundles[0].budget.primary_tokens > 1
    assert result.bundles[0].dispatch_allowed is False
    assert {item.code for item in result.bundles[0].diagnostics} == {
        "context_insufficient",
        "primary_exceeds_budget",
    }


def test_distractor_and_degraded_relations_are_not_selected() -> None:
    source = _snapshot(
        "src/Main.ets",
        "function changed() {}\nfunction sameFileOnly() {}\nfunction uncertain() {}\n",
    )
    primary = _primary(source, 1)
    distractor, distractor_edge = _candidate(
        primary=primary,
        snapshot=source,
        span=_line_range(source.content, 2, 2),
        necessity="distractor",
        relation_type="same_file",
        provenance="fixture:distractor",
    )
    degraded, degraded_edge = _candidate(
        primary=primary,
        snapshot=source,
        span=_line_range(source.content, 3, 3),
        necessity="helpful",
        quality="degraded",
        provenance="fixture:degraded",
    )

    result = _plan(
        primaries=(primary,),
        snapshots=(source,),
        candidates=(degraded, distractor),
        edges=(degraded_edge, distractor_edge),
    )

    assert result.supporting_segments == ()
    assert {
        item.candidate_id: item.reason for item in result.omitted_candidates
    } == {
        distractor.candidate_id: "distractor_rejected",
        degraded.candidate_id: "relation_degraded",
    }
    assert any(item.code == "relation_degraded" for item in result.diagnostics)


def test_input_permutations_do_not_change_context_plan() -> None:
    first_source = _snapshot(
        "src/A.ets",
        "function a() {}\nfunction helperA() {}\n",
    )
    second_source = _snapshot(
        "src/B.ets",
        "function b() {}\nfunction helperB() {}\n",
    )
    first = _primary(first_source, 1, atom_suffix="1")
    second = _primary(second_source, 1, atom_suffix="2")
    first_candidate, first_edge = _candidate(
        primary=first,
        snapshot=first_source,
        span=_line_range(first_source.content, 2, 2),
        provenance="fixture:first",
    )
    second_candidate, second_edge = _candidate(
        primary=second,
        snapshot=second_source,
        span=_line_range(second_source.content, 2, 2),
        provenance="fixture:second",
    )

    forward = _plan(
        primaries=(first, second),
        snapshots=(first_source, second_source),
        candidates=(first_candidate, second_candidate),
        edges=(first_edge, second_edge),
    )
    reverse = _plan(
        primaries=(second, first),
        snapshots=(second_source, first_source),
        candidates=(second_candidate, first_candidate),
        edges=(second_edge, first_edge),
    )

    assert reverse.to_dict() == forward.to_dict()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_binding", "exactly cover"),
        ("extra_snapshot", "exactly cover"),
        ("wrong_tokens", "does not match source span"),
        ("dangling_edge", "dangling RelationEdge"),
        ("source_drift", "immutable source slice"),
    ],
)
def test_planner_fails_closed_on_graph_and_source_drift(
    mutation: str,
    message: str,
) -> None:
    source = _snapshot("src/Main.ets", "function changed() {}\nfunction helper() {}\n")
    primary = _primary(source, 1)
    candidate, edge = _candidate(
        primary=primary,
        snapshot=source,
        span=_line_range(source.content, 2, 2),
    )
    bindings: tuple[QuestionBinding, ...] = (
        QuestionBinding(primary.unit_id, "correctness"),
    )
    snapshots: tuple[CodeSourceSnapshot, ...] = (source,)
    candidates = (candidate,)
    edges: tuple[RelationEdge, ...] = (edge,)
    primaries = (primary,)
    if mutation == "missing_binding":
        bindings = ()
    elif mutation == "extra_snapshot":
        snapshots += (_snapshot("src/Unused.ets", "function unused() {}\n"),)
    elif mutation == "wrong_tokens":
        candidates = (
            ContextCandidate.create(
                primary_unit_id=candidate.primary_unit_id,
                review_question_id=candidate.review_question_id,
                relation_edge_id=candidate.relation_edge_id,
                relation_type=candidate.relation_type,
                target_source_ref_id=candidate.target_source_ref_id,
                target_span=candidate.target_span,
                estimated_tokens=candidate.estimated_tokens + 1,
                necessity=candidate.necessity,
                provenance_ref=candidate.provenance_ref,
            ),
        )
    elif mutation == "dangling_edge":
        edges = ()
    elif mutation == "source_drift":
        primaries = (replace(primary, full_text="function forged() {}"),)

    with pytest.raises(ValueError, match=message):
        ContextPlanner().plan(
            change_set_id=f"change-set:sha256:{'d' * 64}",
            primary_units=primaries,
            primary_question_bindings=bindings,
            source_snapshots=snapshots,
            candidates=candidates,
            relation_edges=edges,
            code_context_budget=1000,
        )


def test_token_estimator_charges_long_comments_strings_unicode_and_trivia() -> None:
    short = "let x = 1"
    long = "// " + ("说明🙂" * 40) + "\nconst value = '" + ("x" * 200) + "';"

    assert estimate_code_tokens("") == 0
    assert estimate_code_tokens(short) > 0
    assert estimate_code_tokens(long) > estimate_code_tokens(short) * 10
    assert estimate_code_tokens(" " * 100) >= 25


def test_candidate_range_rejects_non_utf16_boundary() -> None:
    source = _snapshot("src/Main.ets", "🙂 helper\nfunction changed() {}\n")
    primary = _primary(source, 2)
    invalid_span = ExactRange(1, 1, 1, 2)
    boundary_ref = f"declaration:sha256:{'e' * 64}"
    edge = RelationEdge.create(
        source_ref=primary.unit_id,
        target_ref=source_span_ref_id(source.source_ref.source_ref_id, invalid_span),
        relation_type="direct_call",
        strength="strong",
        quality="exact",
        evidence_refs=(boundary_ref, "fixture:surrogate"),
        provenance_ref="fixture:surrogate",
    )
    candidate = ContextCandidate.create(
        primary_unit_id=primary.unit_id,
        review_question_id="correctness",
        relation_edge_id=edge.edge_id,
        relation_type="direct_call",
        target_source_ref_id=source.source_ref.source_ref_id,
        target_span=invalid_span,
        estimated_tokens=1,
        necessity="required",
        provenance_ref=boundary_ref,
    )

    with pytest.raises(ValueError, match="UTF-16 source boundaries"):
        _plan(
            primaries=(primary,),
            snapshots=(source,),
            candidates=(candidate,),
            edges=(edge,),
        )


def test_result_model_rejects_forged_overflow_diagnostic() -> None:
    source = _snapshot("src/Main.ets", "function changed() {}\n")
    primary = _primary(source, 1)
    result = _plan(primaries=(primary,), snapshots=(source,), budget=100)
    original = result.bundles[0]
    forged_diagnostic = ContextDiagnostic(
        "primary_exceeds_budget",
        (primary.unit_id,),
    )
    forged_bundle = ReviewContextBundle.create(
        group_id=original.group_id,
        primary_unit_ids=original.primary_unit_ids,
        primary_question_bindings=original.primary_question_bindings,
        supporting_segment_ids=original.supporting_segment_ids,
        relation_edge_ids=original.relation_edge_ids,
        budget=original.budget,
        dispatch_allowed=True,
        diagnostics=(forged_diagnostic,),
    )

    with pytest.raises(ValueError, match="diagnostics/dispatch"):
        ContextPlanResult.create(
            change_set_id=result.change_set_id,
            blocking_change_ids=(),
            primary_question_bindings=result.primary_question_bindings,
            candidates=result.candidates,
            supporting_segments=result.supporting_segments,
            relation_edges=result.relation_edges,
            change_groups=result.change_groups,
            bundles=(forged_bundle,),
            omitted_candidates=result.omitted_candidates,
            budget_summary=result.budget_summary,
            diagnostics=(forged_diagnostic,),
        )


def test_result_model_rejects_fit_required_context_forged_as_omitted() -> None:
    source = _snapshot(
        "src/Main.ets",
        "function changed() {}\nfunction required() {}\n",
    )
    primary = _primary(source, 1)
    candidate, edge = _candidate(
        primary=primary,
        snapshot=source,
        span=_line_range(source.content, 2, 2),
    )
    result = _plan(
        primaries=(primary,),
        snapshots=(source,),
        candidates=(candidate,),
        edges=(edge,),
        budget=100,
    )
    original = result.bundles[0]
    insufficient = ContextDiagnostic(
        "context_insufficient",
        (candidate.candidate_id,),
    )
    forged_budget = BundleBudget(
        limit=original.budget.limit,
        primary_tokens=original.budget.primary_tokens,
        supporting_tokens=0,
        total_tokens=original.budget.primary_tokens,
    )
    forged_bundle = ReviewContextBundle.create(
        group_id=original.group_id,
        primary_unit_ids=original.primary_unit_ids,
        primary_question_bindings=original.primary_question_bindings,
        supporting_segment_ids=(),
        relation_edge_ids=(),
        budget=forged_budget,
        dispatch_allowed=False,
        diagnostics=(insufficient,),
    )

    with pytest.raises(ValueError, match="Supporting selection"):
        ContextPlanResult.create(
            change_set_id=result.change_set_id,
            blocking_change_ids=(),
            primary_question_bindings=result.primary_question_bindings,
            candidates=result.candidates,
            supporting_segments=(),
            relation_edges=result.relation_edges,
            change_groups=result.change_groups,
            bundles=(forged_bundle,),
            omitted_candidates=(
                CandidateOmission(candidate.candidate_id, "budget_exceeded"),
            ),
            budget_summary=replace(
                result.budget_summary,
                total_supporting_tokens=0,
                total_omitted_tokens=candidate.estimated_tokens,
                max_bundle_tokens=forged_budget.total_tokens,
                dispatchable_bundles=0,
                blocked_bundles=1,
            ),
            diagnostics=(insufficient,),
        )


def test_result_model_rejects_arbitrary_group_without_strong_edge() -> None:
    first_source = _snapshot("src/A.ets", "function a() {}\n")
    second_source = _snapshot("src/B.ets", "function b() {}\n")
    first = _primary(first_source, 1, atom_suffix="1")
    second = _primary(second_source, 1, atom_suffix="2")
    edge = RelationEdge.create(
        source_ref=first.unit_id,
        target_ref=second.unit_id,
        relation_type="direct_call",
        strength="strong",
        quality="exact",
        evidence_refs=("fixture:call",),
        provenance_ref="fixture:call-graph",
    )
    result = _plan(
        primaries=(first, second),
        snapshots=(first_source, second_source),
        edges=(edge,),
    )
    original_group = result.change_groups[0]
    forged_group = ChangeGroup.create(
        primary_unit_ids=original_group.primary_unit_ids,
        strong_edge_ids=(),
    )
    original_bundle = result.bundles[0]
    forged_bundle = ReviewContextBundle.create(
        group_id=forged_group.group_id,
        primary_unit_ids=original_bundle.primary_unit_ids,
        primary_question_bindings=original_bundle.primary_question_bindings,
        supporting_segment_ids=(),
        relation_edge_ids=(),
        budget=original_bundle.budget,
        dispatch_allowed=True,
        diagnostics=(),
    )

    with pytest.raises(ValueError, match="strong exact relation components"):
        ContextPlanResult.create(
            change_set_id=result.change_set_id,
            blocking_change_ids=(),
            primary_question_bindings=result.primary_question_bindings,
            candidates=(),
            supporting_segments=(),
            relation_edges=(edge,),
            change_groups=(forged_group,),
            bundles=(forged_bundle,),
            omitted_candidates=(),
            budget_summary=result.budget_summary,
            diagnostics=(),
        )


def test_budget_rejects_zero_instead_of_falling_back_to_default() -> None:
    source = _snapshot("src/Main.ets", "function changed() {}\n")
    primary = _primary(source, 1)

    with pytest.raises(ValueError, match="integer >= 1"):
        _plan(primaries=(primary,), snapshots=(source,), budget=0)
