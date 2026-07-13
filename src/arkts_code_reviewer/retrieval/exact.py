from __future__ import annotations

from dataclasses import dataclass, replace

from arkts_code_reviewer.retrieval.applicability import evaluate_applicability
from arkts_code_reviewer.retrieval.config import RetrievalConfig
from arkts_code_reviewer.retrieval.models import (
    ApplicabilityResult,
    EvidenceMatch,
    KnowledgeIndex,
    KnowledgeIndexRecord,
    MatchKind,
    MatchScope,
    RetrievalUnitRequest,
    TargetPlatform,
)


@dataclass(frozen=True)
class ExactHit:
    record: KnowledgeIndexRecord
    rank: int
    score: int
    matched_by: tuple[EvidenceMatch, ...]
    applicability: ApplicabilityResult
    dimension_overlap: int
    authority_priority: int

    def __post_init__(self) -> None:
        if self.rank < 1 or self.score < 1:
            raise ValueError("Exact hit rank and score must be positive")
        if self.applicability not in {"applicable", "unknown"}:
            raise ValueError("Exact hit cannot retain an excluded Clause")


def _api_aliases(index: KnowledgeIndex) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for symbol in index.api_symbols:
        for name in (symbol.canonical_name, *symbol.aliases):
            candidates.setdefault(name, set()).add(symbol.canonical_name)
    return {
        name: next(iter(canonical))
        for name, canonical in candidates.items()
        if len(canonical) == 1
    }


def _canonical_query_apis(index: KnowledgeIndex, apis: tuple[str, ...]) -> set[str]:
    aliases = _api_aliases(index)
    return {aliases.get(api, api) for api in apis}


def search_exact(
    index: KnowledgeIndex,
    unit: RetrievalUnitRequest,
    target: TargetPlatform,
    config: RetrievalConfig,
) -> tuple[ExactHit, ...]:
    query_apis = _canonical_query_apis(index, unit.exact_signals.apis)
    priorities = config.authority_priority_by_id
    hits: list[ExactHit] = []
    searchable_context = " ".join(
        value
        for value in (unit.intent_summary, unit.semantic_code_excerpt)
        if value is not None
    ).casefold()
    for record in index.records:
        evaluation = evaluate_applicability(record.clause.applicability, target)
        if evaluation.decision == "excluded":
            continue
        annotation = record.annotation
        matches: set[tuple[MatchKind, MatchScope, str]] = set()
        score = 0
        if record.clause.rule_id in unit.requested_rule_ids:
            matches.add(("rule_id", "unit_exact", record.clause.rule_id))
            score += config.weights.rule_id
        for api in sorted(query_apis.intersection(annotation.apis)):
            matches.add(("api", "unit_exact", api))
            score += config.weights.api
        for component in sorted(
            set(unit.exact_signals.components).intersection(annotation.components)
        ):
            matches.add(("component", "unit_exact", component))
            score += config.weights.component
        for decorator in sorted(
            set(unit.exact_signals.decorators).intersection(annotation.decorators)
        ):
            matches.add(("decorator", "unit_exact", decorator))
            score += config.weights.decorator
        for tag in sorted(set(unit.exact_tags).intersection(annotation.tags)):
            matches.add(("tag", "unit_exact", tag))
            score += config.weights.exact_tag
        for tag in sorted(
            (set(unit.routing_tags) - set(unit.exact_tags)).intersection(annotation.tags)
        ):
            matches.add(("tag", "file_hint", tag))
            score += config.weights.routing_tag
        for keyword in sorted(set((*annotation.raw_keywords, *annotation.llm_keywords))):
            if keyword.casefold() in searchable_context:
                matches.add(("keyword", "unit_exact", keyword))
                score += config.weights.keyword
        if not matches:
            continue
        dimension_overlap = len(
            set(unit.retrieval_dimension_ids).intersection(annotation.dimension_ids)
        )
        score += dimension_overlap * config.weights.dimension_overlap
        if evaluation.decision == "applicable":
            score += config.weights.applicability_exact
        authority_priority = priorities.get(record.clause.authority, 0)
        evidence_matches = tuple(
            EvidenceMatch(kind=kind, scope=scope, value=value)
            for kind, scope, value in sorted(matches)
        )
        hits.append(
            ExactHit(
                record=record,
                rank=1,
                score=score,
                matched_by=evidence_matches,
                applicability=evaluation.decision,
                dimension_overlap=dimension_overlap,
                authority_priority=authority_priority,
            )
        )
    ordered = sorted(
        hits,
        key=lambda item: (
            -item.score,
            -len(item.matched_by),
            -item.authority_priority,
            item.record.clause.rule_id,
        ),
    )[: config.exact_candidate_limit]
    return tuple(replace(item, rank=rank) for rank, item in enumerate(ordered, start=1))


__all__ = ["ExactHit", "search_exact"]
