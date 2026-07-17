from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Literal, cast

from arkts_code_reviewer.code_analysis.change_review import build_change_review_units
from arkts_code_reviewer.code_analysis.change_set import CodeSourceSnapshot
from arkts_code_reviewer.code_analysis.context_planning import (
    TOKEN_ESTIMATOR_VERSION,
    ContextPlanResult,
    RelationEdge,
    estimate_code_tokens,
)
from arkts_code_reviewer.code_analysis.file_analysis_models import (
    CodeSourceRef,
    DeclarationOccurrence,
    ExactRange,
    FileAnalysis,
    FileParseResult,
    ReviewRegion,
    ScopedFacts,
    UnitFactScope,
)
from arkts_code_reviewer.code_analysis.file_analysis_parser import (
    ArktsFileAnalysisParser,
    FileAnalysisParser,
)
from arkts_code_reviewer.code_analysis.models import AnalysisResult, ReviewUnit
from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder
from arkts_code_reviewer.code_analysis.text_utils import extract_lines
from arkts_code_reviewer.feature_routing.models import (
    FeatureSignal,
    FileSymbolLeafFeatureSignal,
    NormalizedFeatureSignal,
    TagMatch,
    UnitFeatureProfile,
    UnitSymbolLeafOwnerRoleFeatureSignal,
)
from arkts_code_reviewer.feature_routing.owner_context import (
    OwnerContextDiagnostic,
    UnitOwnerContext,
    derive_unit_owner_context,
)
from arkts_code_reviewer.hybrid_analysis._canonical import canonical_hash, canonical_json
from arkts_code_reviewer.hybrid_analysis.models import (
    AI_TAG_MODEL_VIEW_SCHEMA_VERSION,
    REVIEW_UNIT_ANALYSIS_CARD_SCHEMA_VERSION,
    AIModelCode,
    AITagModelView,
    AnalysisCode,
    AnalysisQuality,
    AvailableContextRef,
    BasicStaticFeatureSignal,
    CodeFactSet,
    FileSymbolLeafSignal,
    NormalizedSymbolLeafSignal,
    OwnerIdentity,
    OwnerSummary,
    ReviewUnitAnalysisCard,
    ScopedCodeFacts,
    StaticFeatureSignal,
    StaticTagMatch,
    StaticTagSignals,
    UnitOwnerRoleSymbolSignal,
    project_owner_summary,
    seal_ai_tag_model_view,
    seal_review_unit_analysis_card,
    verify_model_view_against_card,
)

ANALYSIS_CARD_BUILDER_VERSION: Literal["analysis-card-builder-v1"] = (
    "analysis-card-builder-v1"
)
PROVIDER_EGRESS_ANALYSIS_CARD_BUILDER_VERSION: Literal[
    "analysis-card-builder-v2-provider-egress"
] = "analysis-card-builder-v2-provider-egress"
AI_MODEL_VIEW_BUILDER_VERSION: Literal["ai-model-view-builder-v2"] = (
    "ai-model-view-builder-v2"
)


@dataclass(frozen=True)
class AnalysisContextPolicy:
    """Typed, reproducible policy for deterministic Analysis Card construction."""

    builder_version: Literal[
        "analysis-card-builder-v1",
        "analysis-card-builder-v2-provider-egress",
    ] = ANALYSIS_CARD_BUILDER_VERSION
    token_estimator_version: Literal["arkts-code-token-v1"] = "arkts-code-token-v1"
    code_token_budget: int = 2_400
    max_full_unit_lines: int = 160
    max_full_unit_characters: int = 12_000
    changed_window_context_lines: int = 6
    signature_retention: Literal["structured_owner_summary"] = "structured_owner_summary"
    context_ref_policy: Literal["verified_change_correspondence_only"] = (
        "verified_change_correspondence_only"
    )
    redaction_policy: Literal[
        "none_no_provider_dispatch",
        "none_requires_exact_body_runtime_approval",
    ] = "none_no_provider_dispatch"
    parser_verification: Literal["trusted_file_parser_replay"] = (
        "trusted_file_parser_replay"
    )
    review_unit_verification: Literal["canonical_review_unit_replay"] = (
        "canonical_review_unit_replay"
    )

    def __post_init__(self) -> None:
        if self.builder_version not in {
            ANALYSIS_CARD_BUILDER_VERSION,
            PROVIDER_EGRESS_ANALYSIS_CARD_BUILDER_VERSION,
        }:
            raise ValueError("AnalysisContextPolicy.builder_version is unsupported")
        if self.token_estimator_version != TOKEN_ESTIMATOR_VERSION:
            raise ValueError("AnalysisContextPolicy.token_estimator_version is unsupported")
        if self.signature_retention != "structured_owner_summary":
            raise ValueError("AnalysisContextPolicy.signature_retention is unsupported")
        if self.context_ref_policy != "verified_change_correspondence_only":
            raise ValueError("AnalysisContextPolicy.context_ref_policy is unsupported")
        expected_redaction = (
            "none_no_provider_dispatch"
            if self.builder_version == ANALYSIS_CARD_BUILDER_VERSION
            else "none_requires_exact_body_runtime_approval"
        )
        if self.redaction_policy != expected_redaction:
            raise ValueError(
                "AnalysisContextPolicy redaction policy does not match its builder version"
            )
        if self.parser_verification != "trusted_file_parser_replay":
            raise ValueError("AnalysisContextPolicy.parser_verification is unsupported")
        if self.review_unit_verification != "canonical_review_unit_replay":
            raise ValueError(
                "AnalysisContextPolicy.review_unit_verification is unsupported"
            )
        for value, context, minimum in (
            (self.code_token_budget, "AnalysisContextPolicy.code_token_budget", 1),
            (self.max_full_unit_lines, "AnalysisContextPolicy.max_full_unit_lines", 1),
            (
                self.max_full_unit_characters,
                "AnalysisContextPolicy.max_full_unit_characters",
                1,
            ),
            (
                self.changed_window_context_lines,
                "AnalysisContextPolicy.changed_window_context_lines",
                0,
            ),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                raise ValueError(f"{context} must be an integer >= {minimum}")

    @property
    def fingerprint(self) -> str:
        return canonical_hash("analysis-context-policy", asdict(self))


@dataclass(frozen=True)
class AIModelViewProjectionPolicy:
    """Closed whitelist used to project one sealed Card into the model-visible view."""

    builder_version: Literal["ai-model-view-builder-v2"] = AI_MODEL_VIEW_BUILDER_VERSION
    field_projection: Literal["source_role_code_owner_facts_quality_only"] = (
        "source_role_code_owner_facts_quality_only"
    )
    line_rendering: Literal["absolute_line_colon_space"] = "absolute_line_colon_space"

    def __post_init__(self) -> None:
        if self.builder_version != AI_MODEL_VIEW_BUILDER_VERSION:
            raise ValueError("AIModelViewProjectionPolicy.builder_version is unsupported")
        if self.field_projection != "source_role_code_owner_facts_quality_only":
            raise ValueError("AIModelViewProjectionPolicy.field_projection is unsupported")
        if self.line_rendering != "absolute_line_colon_space":
            raise ValueError("AIModelViewProjectionPolicy.line_rendering is unsupported")

    @property
    def fingerprint(self) -> str:
        return canonical_hash("ai-model-view-policy", asdict(self))


DEFAULT_ANALYSIS_CONTEXT_POLICY = AnalysisContextPolicy()
DEFAULT_AI_MODEL_VIEW_PROJECTION_POLICY = AIModelViewProjectionPolicy()


class AnalysisCardBuilder:
    """Build a sealed Analysis Card from one complete, replay-validated upstream graph."""

    def __init__(self) -> None:
        self._file_parser: FileAnalysisParser = ArktsFileAnalysisParser()
        self._parse_replay_cache: dict[str, FileParseResult] = {}

    def build(
        self,
        *,
        analysis_result: AnalysisResult,
        context_plan: ContextPlanResult,
        source_snapshots: Mapping[str, CodeSourceSnapshot],
        unit_id: str,
        policy: AnalysisContextPolicy = DEFAULT_ANALYSIS_CONTEXT_POLICY,
    ) -> ReviewUnitAnalysisCard:
        return self.build_many(
            analysis_result=analysis_result,
            context_plan=context_plan,
            source_snapshots=source_snapshots,
            unit_ids=(unit_id,),
            policy=policy,
        )[0]

    def build_many(
        self,
        *,
        analysis_result: AnalysisResult,
        context_plan: ContextPlanResult,
        source_snapshots: Mapping[str, CodeSourceSnapshot],
        unit_ids: tuple[str, ...] | None = None,
        policy: AnalysisContextPolicy = DEFAULT_ANALYSIS_CONTEXT_POLICY,
    ) -> tuple[ReviewUnitAnalysisCard, ...]:
        upstream = _validate_upstream_bundle(
            analysis_result=analysis_result,
            context_plan=context_plan,
            source_snapshots=source_snapshots,
            file_parser=self._file_parser,
            parse_replay_cache=self._parse_replay_cache,
        )
        selected_unit_ids = (
            tuple(item.unit_id for item in upstream.analysis_result.review_units)
            if unit_ids is None
            else unit_ids
        )
        if (
            not isinstance(selected_unit_ids, tuple)
            or not selected_unit_ids
            or any(
                not isinstance(item, str) or not item or item != item.strip()
                for item in selected_unit_ids
            )
            or len(selected_unit_ids) != len(set(selected_unit_ids))
        ):
            raise ValueError("unit_ids must be a non-empty tuple of unique trimmed IDs")
        return tuple(
            self._build_validated_card(
                graph=_resolve_validated_graph(upstream, unit_id),
                upstream=upstream,
                policy=policy,
            )
            for unit_id in selected_unit_ids
        )

    def _build_validated_card(
        self,
        *,
        graph: _ValidatedGraph,
        upstream: _ValidatedUpstream,
        policy: AnalysisContextPolicy,
    ) -> ReviewUnitAnalysisCard:
        analysis_result = upstream.analysis_result
        context_plan = upstream.context_plan
        code = _build_analysis_code(
            unit=graph.unit,
            snapshot=graph.snapshot,
            analysis_result=analysis_result,
            policy=policy,
        )
        owner = _build_owner_projection(graph.unit, graph.file_analysis, graph.scope)
        static_tags = _build_static_tags(graph.profile)
        facts = _scoped_code_facts(graph.scope.unit_exact, graph.scope.file_hints)
        quality = graph.file_analysis.parser_quality
        context_refs = _available_context_refs(
            context_plan,
            graph.unit.unit_id,
            tuple(analysis_result.review_units),
            upstream.change_set_id,
        )
        payload: dict[str, object] = {
            "schema_version": REVIEW_UNIT_ANALYSIS_CARD_SCHEMA_VERSION,
            "unit_id": graph.unit.unit_id,
            "source_ref_id": graph.source_ref.source_ref_id,
            "feature_profile_id": graph.profile.profile_id,
            "feature_routing_id": analysis_result.feature_routing_result.feature_routing_id,
            "context_plan_id": context_plan.context_plan_id,
            "source_role": graph.unit.source_role,
            "unit_kind": graph.unit.unit_kind,
            "unit_symbol": graph.unit.unit_symbol,
            "owner_summary": owner.summary,
            "code": code,
            "change_atom_ids": tuple(graph.unit.change_atom_ids),
            "exact_occurrence_ids": graph.scope.exact_occurrence_ids,
            "owner_context_occurrence_ids": owner.occurrence_ids,
            "owner_context_declaration_ids": owner.declaration_ids,
            "unit_fact_diagnostics": graph.scope.diagnostics,
            "facts": facts,
            "static_tags": static_tags,
            "quality": AnalysisQuality(
                parser_layer=quality.layer,
                error_nodes=quality.error_nodes,
                missing_nodes=quality.missing_nodes,
                context_degraded=graph.unit.context_degraded,
                unit_owner_unresolved="unit_owner_unresolved" in graph.scope.diagnostics,
            ),
            "available_context_refs": context_refs,
            "code_token_budget": policy.code_token_budget,
            "feature_config_fingerprint": graph.profile.feature_config_version,
            "context_policy_fingerprint": policy.fingerprint,
        }
        return seal_review_unit_analysis_card(payload)


class ModelViewBuilder:
    """Project only the closed model-visible whitelist from a sealed Analysis Card."""

    def build(
        self,
        *,
        card: ReviewUnitAnalysisCard,
        policy: AIModelViewProjectionPolicy = DEFAULT_AI_MODEL_VIEW_PROJECTION_POLICY,
    ) -> AITagModelView:
        if not isinstance(policy, AIModelViewProjectionPolicy):
            raise ValueError("policy must use AIModelViewProjectionPolicy")
        policy.__post_init__()
        card = _revalidate_card(card)
        line_numbers = tuple(range(card.code.line_start, card.code.line_end + 1))
        numbered_text = "\n".join(
            f"{line}: {text}"
            for line, text in zip(
                line_numbers,
                card.code.text.split("\n"),
                strict=True,
            )
        )
        model_view = seal_ai_tag_model_view(
            {
                "schema_version": AI_TAG_MODEL_VIEW_SCHEMA_VERSION,
                "card_id": card.card_id,
                "unit_id": card.unit_id,
                "source_ref_id": card.source_ref_id,
                "source_role": card.source_role,
                "code": AIModelCode(
                    mode=card.code.mode,
                    numbered_text=numbered_text,
                    line_numbers=line_numbers,
                    truncated=card.code.truncated,
                ),
                "owner_summary": project_owner_summary(card.owner_summary),
                "scoped_facts": card.facts,
                "quality": card.quality,
                "projection_policy_fingerprint": policy.fingerprint,
            }
        )
        verify_model_view_against_card(model_view, card)
        return model_view


def build_review_unit_analysis_card(
    *,
    analysis_result: AnalysisResult,
    context_plan: ContextPlanResult,
    source_snapshots: Mapping[str, CodeSourceSnapshot],
    unit_id: str,
    policy: AnalysisContextPolicy = DEFAULT_ANALYSIS_CONTEXT_POLICY,
) -> ReviewUnitAnalysisCard:
    return AnalysisCardBuilder().build(
        analysis_result=analysis_result,
        context_plan=context_plan,
        source_snapshots=source_snapshots,
        unit_id=unit_id,
        policy=policy,
    )


def build_review_unit_analysis_cards(
    *,
    analysis_result: AnalysisResult,
    context_plan: ContextPlanResult,
    source_snapshots: Mapping[str, CodeSourceSnapshot],
    unit_ids: tuple[str, ...] | None = None,
    policy: AnalysisContextPolicy = DEFAULT_ANALYSIS_CONTEXT_POLICY,
) -> tuple[ReviewUnitAnalysisCard, ...]:
    return AnalysisCardBuilder().build_many(
        analysis_result=analysis_result,
        context_plan=context_plan,
        source_snapshots=source_snapshots,
        unit_ids=unit_ids,
        policy=policy,
    )


def build_ai_tag_model_view(
    *,
    card: ReviewUnitAnalysisCard,
    policy: AIModelViewProjectionPolicy = DEFAULT_AI_MODEL_VIEW_PROJECTION_POLICY,
) -> AITagModelView:
    return ModelViewBuilder().build(card=card, policy=policy)


def verify_analysis_card_against_upstream(
    card: ReviewUnitAnalysisCard,
    *,
    analysis_result: AnalysisResult,
    context_plan: ContextPlanResult,
    source_snapshots: Mapping[str, CodeSourceSnapshot],
    policy: AnalysisContextPolicy = DEFAULT_ANALYSIS_CONTEXT_POLICY,
) -> None:
    card = _revalidate_card(card)
    expected = build_review_unit_analysis_card(
        analysis_result=analysis_result,
        context_plan=context_plan,
        source_snapshots=source_snapshots,
        unit_id=card.unit_id,
        policy=policy,
    )
    if card != expected:
        raise ValueError(
            "ReviewUnit Analysis Card does not match the supplied upstream graph and policy"
        )


def verify_model_view_against_card_and_policy(
    model_view: AITagModelView,
    card: ReviewUnitAnalysisCard,
    *,
    policy: AIModelViewProjectionPolicy = DEFAULT_AI_MODEL_VIEW_PROJECTION_POLICY,
) -> None:
    if not isinstance(policy, AIModelViewProjectionPolicy):
        raise ValueError("policy must use AIModelViewProjectionPolicy")
    policy.__post_init__()
    verify_model_view_against_card(model_view, card)
    if model_view.projection_policy_fingerprint != policy.fingerprint:
        raise ValueError("AI Tag Model View does not match the supplied projection policy")


@dataclass(frozen=True)
class _ValidatedGraph:
    unit: ReviewUnit
    scope: UnitFactScope
    profile: UnitFeatureProfile
    file_analysis: FileAnalysis
    snapshot: CodeSourceSnapshot
    source_ref: CodeSourceRef


@dataclass(frozen=True)
class _ValidatedUpstream:
    analysis_result: AnalysisResult
    context_plan: ContextPlanResult
    snapshots: Mapping[str, CodeSourceSnapshot]
    parse_results_by_source: Mapping[str, FileParseResult]
    change_set_id: str


def _validate_upstream_bundle(
    *,
    analysis_result: AnalysisResult,
    context_plan: ContextPlanResult,
    source_snapshots: Mapping[str, CodeSourceSnapshot],
    file_parser: FileAnalysisParser,
    parse_replay_cache: dict[str, FileParseResult],
) -> _ValidatedUpstream:
    if not isinstance(analysis_result, AnalysisResult):
        raise ValueError("analysis_result must use AnalysisResult")
    if not isinstance(context_plan, ContextPlanResult):
        raise ValueError("context_plan must use ContextPlanResult")
    if not isinstance(source_snapshots, Mapping):
        raise ValueError("source_snapshots must be a source_ref_id mapping")
    analysis_result.validate()
    context_plan.__post_init__()
    if (
        analysis_result.change_set is None
        or analysis_result.review_unit_build_result is None
        or analysis_result.review_unit_build_result.schema_version
        != "review-unit-build-v3"
    ):
        raise ValueError(
            "Analysis Card construction requires a complete review-unit-build-v3 AnalysisResult"
        )

    change_set = analysis_result.change_set
    if context_plan.change_set_id != change_set.change_set_id:
        raise ValueError("ContextPlanResult does not reference the AnalysisResult ChangeSet")
    routed_bindings = tuple(
        (item.primary_unit_id, item.review_question_id)
        for item in analysis_result.feature_routing_result.question_bindings
    )
    planned_bindings = tuple(
        (item.primary_unit_id, item.review_question_id)
        for item in context_plan.primary_question_bindings
    )
    if planned_bindings != routed_bindings:
        raise ValueError("ContextPlanResult question bindings differ from Feature Routing")
    expected_unit_ids = {item.unit_id for item in analysis_result.review_units}
    if {item.primary_unit_id for item in context_plan.primary_question_bindings} != (
        expected_unit_ids
    ):
        raise ValueError("ContextPlanResult does not exactly cover AnalysisResult ReviewUnits")

    snapshots = _validated_snapshots(change_set.source_refs, source_snapshots)
    parse_results_by_source: dict[str, FileParseResult] = {}
    for upstream_parse_result in analysis_result.file_parse_results:
        source_ref_id = upstream_parse_result.analysis.source_ref.source_ref_id
        snapshot = snapshots.get(source_ref_id)
        if snapshot is None:
            raise ValueError("FileParseResult source is absent from ChangeSet snapshots")
        replayed = parse_replay_cache.get(source_ref_id)
        if replayed is None:
            replayed = file_parser.parse_file(snapshot.source_ref, snapshot.content)
            _validate_file_analysis_ranges(replayed.analysis, snapshot.content)
            parse_replay_cache[source_ref_id] = replayed
        if replayed != upstream_parse_result:
            raise ValueError(
                "FileAnalysis facts differ from trusted Parser replay over the source "
                "snapshot"
            )
        parse_results_by_source[source_ref_id] = upstream_parse_result

    canonical_build_result = build_change_review_units(
        change_set=change_set,
        source_snapshots=snapshots,
        file_parse_results=parse_results_by_source,
        review_unit_builder=ReviewUnitBuilder(),
    )
    if canonical_build_result != analysis_result.review_unit_build_result:
        raise ValueError(
            "ReviewUnit graph differs from canonical ReviewUnit Builder replay"
        )

    return _ValidatedUpstream(
        analysis_result=analysis_result,
        context_plan=context_plan,
        snapshots=snapshots,
        parse_results_by_source=parse_results_by_source,
        change_set_id=change_set.change_set_id,
    )


def _resolve_validated_graph(
    upstream: _ValidatedUpstream,
    unit_id: str,
) -> _ValidatedGraph:
    analysis_result = upstream.analysis_result
    if not isinstance(unit_id, str) or not unit_id or unit_id != unit_id.strip():
        raise ValueError("unit_id must be a non-empty trimmed string")

    units = [item for item in analysis_result.review_units if item.unit_id == unit_id]
    scopes = [item for item in analysis_result.unit_fact_scopes if item.unit_id == unit_id]
    profiles = [
        item for item in analysis_result.feature_routing_result.units if item.unit_id == unit_id
    ]
    if len(units) != 1 or len(scopes) != 1 or len(profiles) != 1:
        raise ValueError("unit_id must resolve to one Unit, Scope, and Feature Profile")
    unit = units[0]
    scope = scopes[0]
    profile = profiles[0]
    if not isinstance(scope, UnitFactScope):
        raise ValueError("AnalysisResult Unit scope has an invalid runtime type")
    if unit.source_ref_id is None or unit.source_role is None:
        raise ValueError("Analysis Card construction requires a base/head ReviewUnit")
    if not unit.change_atom_ids:
        raise ValueError("Analysis Card construction requires assigned ChangeAtoms")
    selected_parse_result = upstream.parse_results_by_source.get(unit.source_ref_id)
    if selected_parse_result is None:
        raise ValueError("ReviewUnit source must resolve to one FileAnalysis")
    file_analysis = selected_parse_result.analysis
    file_analysis.validate()
    snapshot = upstream.snapshots[unit.source_ref_id]
    source_ref = snapshot.source_ref
    if not (
        scope.source_ref_id
        == profile.source_ref_id
        == file_analysis.source_ref.source_ref_id
        == source_ref.source_ref_id
    ):
        raise ValueError("Unit, Scope, Profile, FileAnalysis, and snapshot sources disagree")
    if unit.unit_kind != "fallback" and unit.context_span != unit.source_span:
        raise ValueError(
            "non-fallback ReviewUnit context span must equal its source span"
        )
    expected_text = extract_lines(
        snapshot.content,
        unit.context_span.start_line,
        unit.context_span.end_line,
    )
    if unit.full_text != expected_text:
        raise ValueError("ReviewUnit.full_text differs from its immutable source snapshot")
    if unit.file != source_ref.path:
        raise ValueError("ReviewUnit path differs from its immutable source snapshot")
    return _ValidatedGraph(
        unit=unit,
        scope=scope,
        profile=profile,
        file_analysis=file_analysis,
        snapshot=snapshot,
        source_ref=source_ref,
    )


def _validated_snapshots(
    source_refs: tuple[CodeSourceRef, ...],
    source_snapshots: Mapping[str, CodeSourceSnapshot],
) -> dict[str, CodeSourceSnapshot]:
    expected = {item.source_ref_id: item for item in source_refs}
    if set(source_snapshots) != set(expected):
        raise ValueError("source_snapshots must exactly cover ChangeSet.source_refs")
    result: dict[str, CodeSourceSnapshot] = {}
    for source_ref_id, source_ref in expected.items():
        snapshot = source_snapshots[source_ref_id]
        if not isinstance(snapshot, CodeSourceSnapshot):
            raise ValueError("source_snapshots must contain CodeSourceSnapshot values")
        if snapshot.source_ref != source_ref:
            raise ValueError("CodeSourceSnapshot source_ref differs from ChangeSet source")
        source_ref.verify_content(snapshot.content)
        result[source_ref_id] = snapshot
    return result


def _validate_file_analysis_ranges(file_analysis: FileAnalysis, source: str) -> None:
    boundaries = _utf16_source_boundaries(source)
    for declaration in file_analysis.declarations:
        _validate_exact_range(declaration.exact_range, source, boundaries)
    for region in file_analysis.review_regions:
        _validate_exact_range(region.exact_range, source, boundaries)
    for occurrence in file_analysis.fact_occurrences:
        _validate_exact_range(occurrence.exact_range, source, boundaries)


def _utf16_source_boundaries(source: str) -> dict[int, tuple[int, int]]:
    boundaries = {0: (0, 1)}
    offset = 0
    line = 1
    for index, character in enumerate(source, start=1):
        offset += 2 if ord(character) > 0xFFFF else 1
        if character == "\n":
            line += 1
        boundaries[offset] = (index, line)
    return boundaries


def _validate_exact_range(
    span: ExactRange,
    source: str,
    boundaries: Mapping[int, tuple[int, int]],
) -> None:
    try:
        _, start_line = boundaries[span.start_offset_utf16]
        end_index, mapped_end_line = boundaries[span.end_offset_utf16]
    except KeyError as exc:
        raise ValueError("FileAnalysis exact range is not on UTF-16 source boundaries") from exc
    ends_after_declared_newline = (
        end_index > 0
        and source[end_index - 1] == "\n"
        and mapped_end_line == span.end_line + 1
    )
    if start_line != span.start_line or not (
        mapped_end_line == span.end_line or ends_after_declared_newline
    ):
        raise ValueError("FileAnalysis exact range line and UTF-16 offsets disagree")


def _build_analysis_code(
    *,
    unit: ReviewUnit,
    snapshot: CodeSourceSnapshot,
    analysis_result: AnalysisResult,
    policy: AnalysisContextPolicy,
) -> AnalysisCode:
    if not isinstance(policy, AnalysisContextPolicy):
        raise ValueError("policy must use AnalysisContextPolicy")
    policy.__post_init__()
    changed_lines = (
        tuple(unit.changed_old_lines)
        if unit.source_role == "base"
        else tuple(unit.changed_new_lines)
    )
    if not changed_lines:
        raise ValueError("Analysis Card code requires at least one changed line")
    if any(not unit.context_span.contains_line(line) for line in changed_lines):
        raise ValueError("ReviewUnit changed lines must stay inside its context span")

    full_text = extract_lines(
        snapshot.content,
        unit.context_span.start_line,
        unit.context_span.end_line,
    )
    full_line_count = unit.context_span.end_line - unit.context_span.start_line + 1
    full_allowed = (
        full_line_count <= policy.max_full_unit_lines
        and len(full_text) <= policy.max_full_unit_characters
        and estimate_code_tokens(full_text) <= policy.code_token_budget
    )
    if full_allowed:
        line_start = unit.context_span.start_line
        line_end = unit.context_span.end_line
        text = full_text
    else:
        line_start, line_end, text = _select_changed_window(
            snapshot.content,
            unit,
            changed_lines,
            policy,
        )
    truncated = (
        line_start != unit.context_span.start_line
        or line_end != unit.context_span.end_line
    )
    if estimate_code_tokens(text) > policy.code_token_budget:
        raise ValueError("Analysis Card code exceeds its declared token budget")
    deletion_base = _is_deleted_file_base(unit, analysis_result)
    mode: Literal["full_unit", "changed_window", "deletion_base"]
    if deletion_base:
        mode = "deletion_base"
    elif truncated:
        mode = "changed_window"
    else:
        mode = "full_unit"
    return AnalysisCode(
        mode=mode,
        text=text,
        line_start=line_start,
        line_end=line_end,
        changed_line_numbers=changed_lines,
        truncated=truncated,
    )


def _select_changed_window(
    source: str,
    unit: ReviewUnit,
    changed_lines: tuple[int, ...],
    policy: AnalysisContextPolicy,
) -> tuple[int, int, str]:
    for radius in range(policy.changed_window_context_lines, -1, -1):
        line_start = max(unit.context_span.start_line, changed_lines[0] - radius)
        line_end = min(unit.context_span.end_line, changed_lines[-1] + radius)
        text = extract_lines(source, line_start, line_end)
        if (
            line_end - line_start + 1 <= policy.max_full_unit_lines
            and len(text) <= policy.max_full_unit_characters
            and estimate_code_tokens(text) <= policy.code_token_budget
        ):
            return line_start, line_end, text
    raise ValueError(
        "minimum continuous changed-line window exceeds Analysis Card policy limits"
    )


def _is_deleted_file_base(unit: ReviewUnit, analysis_result: AnalysisResult) -> bool:
    if unit.source_role != "base" or analysis_result.change_set is None:
        return False
    atoms_by_id = {item.atom_id: item for item in analysis_result.change_set.atoms}
    try:
        referenced_atoms = tuple(atoms_by_id[item] for item in unit.change_atom_ids)
    except KeyError as exc:
        raise ValueError("ReviewUnit references a ChangeAtom absent from ChangeSet") from exc
    return bool(referenced_atoms) and all(item.kind == "deletion" for item in referenced_atoms)


@dataclass(frozen=True)
class _OwnerProjection:
    summary: OwnerSummary
    occurrence_ids: tuple[str, ...]
    declaration_ids: tuple[str, ...]


def _build_owner_projection(
    unit: ReviewUnit,
    file_analysis: FileAnalysis,
    scope: UnitFactScope,
) -> _OwnerProjection:
    if unit.unit_kind == "fallback":
        return _OwnerProjection(
            summary=OwnerSummary(
                resolution="not_applicable",
                unit_owner=None,
                enclosing_owner=None,
                owner_roles=(),
                diagnostics=(),
            ),
            occurrence_ids=(),
            declaration_ids=(),
        )
    if "unit_owner_unresolved" in scope.diagnostics:
        return _OwnerProjection(
            summary=OwnerSummary(
                resolution="unresolved",
                unit_owner=None,
                enclosing_owner=None,
                owner_roles=(),
                diagnostics=("owner_context_unit_unresolved",),
            ),
            occurrence_ids=(),
            declaration_ids=(),
        )

    unit_owner = _resolve_unit_owner(unit, file_analysis)
    if unit.unit_kind not in {"method", "struct", "class"}:
        return _OwnerProjection(
            summary=OwnerSummary(
                resolution="not_applicable",
                unit_owner=unit_owner,
                enclosing_owner=None,
                owner_roles=(),
                diagnostics=(),
            ),
            occurrence_ids=(),
            declaration_ids=(),
        )

    owner_context = derive_unit_owner_context(file_analysis, unit)
    roles = tuple(sorted({item.owner_role for item in owner_context.evidence}))
    diagnostics: tuple[OwnerContextDiagnostic, ...] = owner_context.diagnostics
    enclosing_owner = _resolve_enclosing_owner(
        unit,
        unit_owner,
        owner_context,
        file_analysis,
    )
    if owner_context.evidence and diagnostics:
        resolution: Literal["resolved", "partial", "unresolved"] = "partial"
    elif diagnostics:
        resolution = "unresolved"
    else:
        resolution = "resolved"
    occurrence_ids = tuple(
        sorted(
            {
                occurrence_id
                for item in owner_context.evidence
                for occurrence_id in item.role_evidence_occurrence_ids
            }
        )
    )
    declaration_ids = tuple(
        sorted(
            {
                declaration_id
                for item in owner_context.evidence
                for declaration_id in (
                    item.direct_owner_declaration_id,
                    item.enclosing_owner_declaration_id,
                )
            }
        )
    )
    return _OwnerProjection(
        summary=OwnerSummary(
            resolution=resolution,
            unit_owner=unit_owner,
            enclosing_owner=enclosing_owner,
            owner_roles=roles,
            diagnostics=diagnostics,
        ),
        occurrence_ids=occurrence_ids,
        declaration_ids=declaration_ids,
    )


def _resolve_unit_owner(unit: ReviewUnit, file_analysis: FileAnalysis) -> OwnerIdentity:
    owner_ref = unit.owner_ref
    if owner_ref is None:
        raise ValueError("non-fallback ReviewUnit requires an occurrence-backed owner")
    if owner_ref.kind == "declaration":
        declaration_matches = [
            item for item in file_analysis.declarations if item.declaration_id == owner_ref.ref_id
        ]
        if len(declaration_matches) != 1:
            raise ValueError("ReviewUnit owner_ref does not resolve to one declaration")
        declaration = declaration_matches[0]
        identity = _declaration_identity(declaration)
    else:
        region_matches = [
            item
            for item in file_analysis.review_regions
            if item.region_id == owner_ref.ref_id
        ]
        if len(region_matches) != 1:
            raise ValueError("ReviewUnit owner_ref does not resolve to one review region")
        region = region_matches[0]
        identity = _region_identity(region)
    if identity.owner_kind != unit.unit_kind or identity.qualified_name != unit.unit_symbol:
        raise ValueError("ReviewUnit owner identity differs from Unit kind or symbol")
    return identity


def _resolve_enclosing_owner(
    unit: ReviewUnit,
    unit_owner: OwnerIdentity,
    owner_context: UnitOwnerContext,
    file_analysis: FileAnalysis,
) -> OwnerIdentity | None:
    declarations = {item.declaration_id: item for item in file_analysis.declarations}
    evidence_enclosing_ids = {
        item.enclosing_owner_declaration_id for item in owner_context.evidence
    }
    if len(evidence_enclosing_ids) > 1:
        raise ValueError("owner-role evidence names multiple enclosing owners")
    if evidence_enclosing_ids:
        enclosing_id = next(iter(evidence_enclosing_ids))
        enclosing = declarations.get(enclosing_id)
        if enclosing is None:
            raise ValueError("owner-role evidence enclosing declaration is missing")
        return _declaration_identity(enclosing)
    if unit.unit_kind == "struct":
        return unit_owner
    if unit.unit_kind == "method" and unit.owner_ref is not None:
        direct = declarations.get(unit.owner_ref.ref_id)
        if direct is not None and direct.parent_id is not None:
            parent = declarations.get(direct.parent_id)
            if parent is not None:
                return _declaration_identity(parent)
    return None


def _declaration_identity(item: DeclarationOccurrence) -> OwnerIdentity:
    return OwnerIdentity(
        kind="declaration",
        ref_id=item.declaration_id,
        owner_kind=cast(
            Literal[
                "struct",
                "class",
                "function",
                "method",
                "build_method",
                "builder",
                "ui_block",
            ],
            item.kind,
        ),
        qualified_name=item.qualified_name,
        quality=item.quality,
    )


def _region_identity(item: ReviewRegion) -> OwnerIdentity:
    return OwnerIdentity(
        kind="region",
        ref_id=item.region_id,
        owner_kind=item.kind,
        qualified_name=item.symbol,
        quality=item.quality,
    )


def _scoped_code_facts(unit_exact: ScopedFacts, file_hints: ScopedFacts) -> ScopedCodeFacts:
    return ScopedCodeFacts(
        unit_exact=_code_fact_set(unit_exact),
        file_hints=_code_fact_set(file_hints),
    )


def _code_fact_set(facts: ScopedFacts) -> CodeFactSet:
    return CodeFactSet(
        apis=facts.apis,
        components=facts.components,
        decorators=facts.decorators,
        attributes=facts.attributes,
        symbols=facts.symbols,
        syntax=facts.syntax,
        calls=facts.calls,
        import_bindings=facts.import_bindings,
        import_uses=facts.import_uses,
        field_reads=facts.field_reads,
        field_writes=facts.field_writes,
        string_literals=facts.string_literals,
        resource_references=facts.resource_references,
    )


def _build_static_tags(profile: UnitFeatureProfile) -> StaticTagSignals:
    matches = tuple(
        sorted(
            (_convert_tag_match(item) for item in profile.tag_matches if item.status == "Active"),
            key=lambda item: (item.scope, item.tag_id),
        )
    )
    static_tags = StaticTagSignals(
        exact=profile.exact_tags,
        routing=profile.routing_tags,
        matches=matches,
    )
    if static_tags.exact != profile.exact_tags or static_tags.routing != profile.routing_tags:
        raise ValueError("converted Active TagMatches do not reproduce Feature Profile Tags")
    return static_tags


def _convert_tag_match(match: TagMatch) -> StaticTagMatch:
    signals = tuple(
        sorted(
            (_convert_signal(item) for item in match.signals),
            key=lambda item: canonical_json(item.model_dump(mode="json")),
        )
    )
    return StaticTagMatch(
        tag_id=match.tag_id,
        status="Active",
        scope=match.scope,
        signals=signals,
    )


def _convert_signal(signal: FeatureSignal) -> StaticFeatureSignal:
    if type(signal) is FeatureSignal:
        return BasicStaticFeatureSignal(
            signal_type="basic",
            kind=signal.kind,
            value=signal.value,
        )
    if type(signal) is NormalizedFeatureSignal:
        normalized = signal
        return NormalizedSymbolLeafSignal(
            signal_type="normalized_symbol_leaf",
            kind="symbols",
            value=normalized.value,
            operator=normalized.operator,
            normalized_value=normalized.normalized_value,
        )
    if type(signal) is FileSymbolLeafFeatureSignal:
        file_signal = signal
        return FileSymbolLeafSignal(
            signal_type="file_symbol_leaf",
            kind="symbols",
            value=file_signal.value,
            operator=file_signal.operator,
            normalized_value=file_signal.normalized_value,
        )
    if type(signal) is UnitSymbolLeafOwnerRoleFeatureSignal:
        owner_signal = signal
        return UnitOwnerRoleSymbolSignal(
            signal_type="unit_owner_role_symbol",
            kind="symbols",
            value=owner_signal.value,
            operator=owner_signal.operator,
            normalized_value=owner_signal.normalized_value,
            owner_role=owner_signal.owner_role,
            symbol_occurrence_id=owner_signal.symbol_occurrence_id,
            direct_owner_declaration_id=owner_signal.direct_owner_declaration_id,
            enclosing_owner_declaration_id=owner_signal.enclosing_owner_declaration_id,
            role_evidence_occurrence_ids=owner_signal.role_evidence_occurrence_ids,
        )
    raise ValueError("unsupported FeatureSignal implementation")


def _available_context_refs(
    context_plan: ContextPlanResult,
    unit_id: str,
    units: tuple[ReviewUnit, ...],
    change_set_id: str,
) -> tuple[AvailableContextRef, ...]:
    base_units = tuple(item for item in units if item.source_role == "base")
    head_units = tuple(item for item in units if item.source_role == "head")
    expected_edges = tuple(
        sorted(
            (
                RelationEdge.create(
                    source_ref=base.unit_id,
                    target_ref=head.unit_id,
                    relation_type="change_correspondence",
                    strength="strong",
                    quality="exact",
                    evidence_refs=tuple(
                        sorted(
                            set(base.change_atom_ids).intersection(head.change_atom_ids)
                        )
                    ),
                    provenance_ref=change_set_id,
                )
                for base in base_units
                for head in head_units
                if set(base.change_atom_ids).intersection(head.change_atom_ids)
            ),
            key=lambda item: item.edge_id,
        )
    )
    actual_edges = tuple(
        edge
        for edge in context_plan.relation_edges
        if edge.relation_type == "change_correspondence"
    )
    if actual_edges != expected_edges:
        raise ValueError(
            "ContextPlanResult change-correspondence relations differ from ChangeSet Units"
        )
    return tuple(
        sorted(
            (
                AvailableContextRef(
                    relation_edge_id=edge.edge_id,
                    target_unit_id=edge.target_ref,
                    relation_type=edge.relation_type,
                )
                for edge in expected_edges
                if edge.source_ref == unit_id
            ),
            key=lambda item: (
                item.relation_type,
                item.target_unit_id,
                item.relation_edge_id,
            ),
        )
    )


def _revalidate_card(card: ReviewUnitAnalysisCard) -> ReviewUnitAnalysisCard:
    if not isinstance(card, ReviewUnitAnalysisCard):
        raise ValueError("card must use ReviewUnitAnalysisCard")
    try:
        return ReviewUnitAnalysisCard.model_validate(card.model_dump(mode="json"))
    except Exception as exc:
        raise ValueError(f"invalid ReviewUnit Analysis Card: {exc}") from exc


__all__ = [
    "AI_MODEL_VIEW_BUILDER_VERSION",
    "ANALYSIS_CARD_BUILDER_VERSION",
    "PROVIDER_EGRESS_ANALYSIS_CARD_BUILDER_VERSION",
    "DEFAULT_AI_MODEL_VIEW_PROJECTION_POLICY",
    "DEFAULT_ANALYSIS_CONTEXT_POLICY",
    "AIModelViewProjectionPolicy",
    "AnalysisCardBuilder",
    "AnalysisContextPolicy",
    "ModelViewBuilder",
    "build_ai_tag_model_view",
    "build_review_unit_analysis_card",
    "build_review_unit_analysis_cards",
    "verify_analysis_card_against_upstream",
    "verify_model_view_against_card_and_policy",
]
