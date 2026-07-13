from __future__ import annotations

from collections.abc import Mapping

from arkts_code_reviewer.code_analysis.change_set import ChangeSet
from arkts_code_reviewer.code_analysis.context_planning import ContextPlanResult
from arkts_code_reviewer.code_analysis.file_analysis_models import (
    FileParseResult,
    UnitFactScope,
)
from arkts_code_reviewer.code_analysis.models import (
    ANALYSIS_RESULT_SCHEMA_VERSION,
    AnalysisResult,
    ReviewUnit,
    ReviewUnitBuildResult,
)
from arkts_code_reviewer.code_analysis.unit_facts import project
from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.feature_routing.models import FeatureRoutingResult
from arkts_code_reviewer.retrieval.models import (
    ParserContextQuality,
    RetrievalRequest,
    RetrievalUnitRequest,
    TargetPlatform,
    UnitExactSignals,
)

_SEMANTIC_EXCERPT_LINE_LIMIT = 16
_SEMANTIC_EXCERPT_CHARACTER_LIMIT = 1600


def _evenly_select(values: tuple[int, ...], limit: int) -> tuple[int, ...]:
    if len(values) <= limit:
        return values
    if limit < 2:
        return values[:limit]
    positions = {
        round(index * (len(values) - 1) / (limit - 1))
        for index in range(limit)
    }
    return tuple(values[position] for position in sorted(positions))


def _semantic_code_excerpt(review_unit: ReviewUnit) -> str | None:
    """Return a small deterministic changed-code view for semantic retrieval."""

    lines = review_unit.full_text.splitlines()
    if not lines:
        return None
    changed_lines = (
        review_unit.changed_new_lines
        or review_unit.changed_old_lines
        or review_unit.unit_changed_lines
    )
    selected_indexes: set[int] = set()
    for absolute_line in changed_lines:
        relative = absolute_line - review_unit.context_span.start_line
        if not 0 <= relative < len(lines):
            continue
        for index in range(max(0, relative - 1), min(len(lines), relative + 2)):
            selected_indexes.add(index)
    if not selected_indexes:
        selected_indexes.update(range(len(lines)))
    ordered_indexes = _evenly_select(
        tuple(sorted(selected_indexes)),
        _SEMANTIC_EXCERPT_LINE_LIMIT,
    )
    rendered: list[str] = []
    for index in ordered_indexes:
        normalized = " ".join(lines[index].split())
        if normalized:
            absolute_line = review_unit.context_span.start_line + index
            rendered.append(f"L{absolute_line}: {normalized}")
    excerpt = " | ".join(rendered)
    if not excerpt:
        return None
    if len(excerpt) > _SEMANTIC_EXCERPT_CHARACTER_LIMIT:
        excerpt = excerpt[: _SEMANTIC_EXCERPT_CHARACTER_LIMIT - 1].rstrip() + "…"
    return excerpt


def _intent_summary(
    review_unit: ReviewUnit,
    signals: UnitExactSignals,
    exact_tags: tuple[str, ...],
    review_question_ids: tuple[str, ...],
) -> str:
    feature_config = load_default_feature_config()
    question_titles = tuple(
        feature_config.review_questions_by_id[question_id].title
        for question_id in review_question_ids
    )
    parts = [
        "ArkTS review unit",
        f"kind: {review_unit.unit_kind}",
        f"symbol: {review_unit.unit_symbol}",
    ]
    for label, values in (
        ("components", signals.components),
        ("apis", signals.apis),
        ("decorators", signals.decorators),
        ("attributes", signals.attributes),
        ("syntax", signals.syntax),
        ("symbols", signals.symbols),
        ("calls", signals.calls),
        ("resources", signals.resource_references),
        ("tags", exact_tags),
        ("review questions", question_titles),
    ):
        if values:
            parts.append(f"{label}: {', '.join(values[:8])}")
    summary = "; ".join(parts)
    if any(ord(character) < 32 for character in summary):
        raise ValueError("formal Retrieval semantic inputs contain control characters")
    return summary


def _unit_budgets(total: int, unit_ids: tuple[str, ...]) -> dict[str, int]:
    if (
        not unit_ids
        or not isinstance(total, int)
        or isinstance(total, bool)
        or total < len(unit_ids)
    ):
        raise ValueError("knowledge token budget must provide at least one token per Unit")
    base, remainder = divmod(total, len(unit_ids))
    return {
        unit_id: base + int(index < remainder)
        for index, unit_id in enumerate(unit_ids)
    }


def _validate_formal_analysis_graph(value: AnalysisResult) -> None:
    """Validate only the formal Retrieval inputs, never the compatibility view."""

    if value.schema_version != ANALYSIS_RESULT_SCHEMA_VERSION:
        raise ValueError("AnalysisResult schema version is unsupported")
    if not isinstance(value.change_set, ChangeSet):
        raise ValueError("formal Retrieval requires a ChangeSet AnalysisResult")
    value.change_set.validate()
    if not isinstance(value.review_units, list) or any(
        not isinstance(item, ReviewUnit) for item in value.review_units
    ):
        raise ValueError("formal Retrieval requires ReviewUnit values")
    if not isinstance(value.unit_fact_scopes, list) or any(
        not isinstance(item, UnitFactScope) for item in value.unit_fact_scopes
    ):
        raise ValueError("formal Retrieval requires UnitFactScope values")
    if not isinstance(value.file_parse_results, list) or any(
        not isinstance(item, FileParseResult) for item in value.file_parse_results
    ):
        raise ValueError("formal Retrieval requires FileParseResult values")
    if not isinstance(value.feature_routing_result, FeatureRoutingResult):
        raise ValueError("formal Retrieval requires FeatureRoutingResult")
    if not isinstance(value.review_unit_build_result, ReviewUnitBuildResult):
        raise ValueError("formal Retrieval requires a ReviewUnit build result")
    if (
        value.review_unit_build_result.schema_version != "review-unit-build-v3"
        or value.review_unit_build_result.change_set_id != value.change_set.change_set_id
        or value.review_unit_build_result.flatten_units() != value.review_units
    ):
        raise ValueError("formal Retrieval ReviewUnits do not match the ChangeSet build")

    unit_ids = [item.unit_id for item in value.review_units]
    scope_ids = [item.unit_id for item in value.unit_fact_scopes]
    if not unit_ids or scope_ids != unit_ids or len(scope_ids) != len(set(scope_ids)):
        raise ValueError("formal Retrieval Unit scopes do not align with ReviewUnits")

    parse_keys = [
        (
            item.analysis.source_ref.path,
            item.analysis.source_ref.revision,
            item.analysis.source_ref.source_ref_id,
        )
        for item in value.file_parse_results
    ]
    parse_source_ids = [item[2] for item in parse_keys]
    if parse_keys != sorted(parse_keys) or len(parse_source_ids) != len(
        set(parse_source_ids)
    ):
        raise ValueError("formal Retrieval parse results must use stable unique order")
    parse_by_source = {
        item.analysis.source_ref.source_ref_id: item for item in value.file_parse_results
    }
    for unit, scope in zip(value.review_units, value.unit_fact_scopes, strict=True):
        unit.validate()
        if unit.source_ref_id != scope.source_ref_id:
            raise ValueError("formal Retrieval Unit scopes disagree about source identity")
        parse_result = parse_by_source.get(scope.source_ref_id)
        if parse_result is None:
            raise ValueError("formal Retrieval Unit source has no parse result")
        parse_result.analysis.validate()
        if scope != project(parse_result.analysis, unit):
            raise ValueError("formal Retrieval Unit facts do not replay from FileAnalysis")

    routing = value.feature_routing_result
    routing.validate_replay(value.unit_fact_scopes)
    profiles = {item.unit_id: item for item in routing.units}
    if set(profiles) != set(scope_ids) or any(
        profiles[scope.unit_id].source_ref_id != scope.source_ref_id
        for scope in value.unit_fact_scopes
    ):
        raise ValueError("formal Retrieval Feature Routing does not align with Unit scopes")


def _normalize_requested_rules(
    value: Mapping[str, tuple[str, ...]] | None,
) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("requested_rule_ids_by_unit must be a mapping")
    normalized: dict[str, tuple[str, ...]] = {}
    for unit_id, rule_ids in value.items():
        if not isinstance(unit_id, str) or not unit_id or unit_id != unit_id.strip():
            raise ValueError("requested rule Unit IDs must be non-empty and trimmed")
        if not isinstance(rule_ids, tuple) or any(
            not isinstance(rule_id, str)
            or not rule_id
            or rule_id != rule_id.strip()
            for rule_id in rule_ids
        ):
            raise ValueError("requested rule IDs must be tuples of non-empty trimmed strings")
        normalized[unit_id] = tuple(sorted(set(rule_ids)))
    return normalized


def build_retrieval_request(
    analysis_result: AnalysisResult,
    context_plan: ContextPlanResult,
    *,
    target_platform: TargetPlatform,
    resolved_index_version: str,
    knowledge_token_budget: int,
    requested_rule_ids_by_unit: Mapping[str, tuple[str, ...]] | None = None,
) -> RetrievalRequest:
    """Build the formal Retrieval input from the routed and planned graph.

    The compatibility ``AnalysisResult.retrieval_query`` is intentionally not read.
    """

    if not isinstance(analysis_result, AnalysisResult):
        raise TypeError("analysis_result must use AnalysisResult")
    if not isinstance(context_plan, ContextPlanResult):
        raise TypeError("context_plan must use ContextPlanResult")
    if not isinstance(target_platform, TargetPlatform):
        raise TypeError("target_platform must use TargetPlatform")
    _validate_formal_analysis_graph(analysis_result)
    context_plan.__post_init__()
    if analysis_result.change_set is None:  # narrowed by formal graph validation
        raise AssertionError("formal graph validation lost its ChangeSet")
    if context_plan.change_set_id != analysis_result.change_set.change_set_id:
        raise ValueError("Context Plan and AnalysisResult reference different ChangeSets")

    routing = analysis_result.feature_routing_result
    expected_bindings = tuple(
        (item.primary_unit_id, item.review_question_id)
        for item in routing.question_bindings
    )
    actual_bindings = tuple(
        (item.primary_unit_id, item.review_question_id)
        for item in context_plan.primary_question_bindings
    )
    if actual_bindings != expected_bindings:
        raise ValueError("Context Plan question bindings do not match Feature Routing")

    profiles = {item.unit_id: item for item in routing.units}
    scopes = {item.unit_id: item for item in analysis_result.unit_fact_scopes}
    review_units = {item.unit_id: item for item in analysis_result.review_units}
    quality_by_source = {
        item.analysis.source_ref.source_ref_id: item.analysis.parser_quality
        for item in analysis_result.file_parse_results
    }
    question_ids_by_unit: dict[str, set[str]] = {}
    for unit_id, question_id in actual_bindings:
        question_ids_by_unit.setdefault(unit_id, set()).add(question_id)
    unit_ids = tuple(sorted(question_ids_by_unit))
    budgets = _unit_budgets(knowledge_token_budget, unit_ids)

    dispatchable_bindings = {
        (binding.primary_unit_id, binding.review_question_id)
        for bundle in context_plan.bundles
        if bundle.dispatch_allowed
        for binding in bundle.primary_question_bindings
    }
    explicit_rules = _normalize_requested_rules(requested_rule_ids_by_unit)
    unknown_rule_units = sorted(set(explicit_rules) - set(unit_ids))
    if unknown_rule_units:
        raise ValueError(
            f"requested_rule_ids_by_unit contains unknown Units: {unknown_rule_units}"
        )

    units: list[RetrievalUnitRequest] = []
    for unit_id in unit_ids:
        profile = profiles.get(unit_id)
        scope = scopes.get(unit_id)
        review_unit = review_units.get(unit_id)
        if profile is None or scope is None or review_unit is None:
            raise ValueError("Retrieval primary Unit is missing routed analysis state")
        parser_quality = quality_by_source.get(scope.source_ref_id)
        if parser_quality is None:
            raise ValueError("Retrieval Unit source has no parser quality record")
        exact = scope.unit_exact
        signals = UnitExactSignals(
            apis=exact.apis,
            components=exact.components,
            decorators=exact.decorators,
            attributes=exact.attributes,
            symbols=exact.symbols,
            syntax=exact.syntax,
            calls=exact.calls,
            resource_references=exact.resource_references,
        )
        review_question_ids = tuple(sorted(question_ids_by_unit[unit_id]))
        dispatchable_questions = tuple(
            question_id
            for question_id in review_question_ids
            if (unit_id, question_id) in dispatchable_bindings
        )
        requested_rules = explicit_rules.get(unit_id, ())
        units.append(
            RetrievalUnitRequest(
                unit_id=unit_id,
                source_ref_id=scope.source_ref_id,
                profile_id=profile.profile_id,
                review_question_ids=review_question_ids,
                dispatchable_review_question_ids=dispatchable_questions,
                exact_signals=signals,
                exact_tags=profile.exact_tags,
                routing_tags=profile.routing_tags,
                retrieval_dimension_ids=profile.retrieval_dimensions,
                routing_dimension_ids=profile.routing_dimensions,
                requested_rule_ids=requested_rules,
                semantic_code_excerpt=_semantic_code_excerpt(review_unit),
                intent_summary=_intent_summary(
                    review_unit,
                    signals,
                    profile.exact_tags,
                    review_question_ids,
                ),
                quality=ParserContextQuality(
                    parser_layer=parser_quality.layer,
                    context_degraded=review_unit.context_degraded,
                    error_nodes=parser_quality.error_nodes,
                    missing_nodes=parser_quality.missing_nodes,
                ),
                knowledge_token_budget=budgets[unit_id],
            )
        )
    return RetrievalRequest.create(
        context_plan_id=context_plan.context_plan_id,
        feature_routing_id=routing.feature_routing_id,
        feature_config_version=routing.feature_config_version,
        index_version=resolved_index_version,
        target_platform=target_platform,
        total_knowledge_token_budget=knowledge_token_budget,
        units=tuple(units),
    )


__all__ = ["build_retrieval_request"]
