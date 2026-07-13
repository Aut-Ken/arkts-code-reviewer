from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from arkts_code_reviewer.retrieval.index import (
    EmbeddingProvider,
    canonical_pgvector_embedding,
)
from arkts_code_reviewer.retrieval.models import (
    KnowledgeIndex,
    RetrievalRequest,
)
from arkts_code_reviewer.retrieval.service import RetrievalService
from arkts_code_reviewer.retrieval_validation.golden import (
    RetrievalGoldenCase,
    load_retrieval_golden_manifest,
)


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}:sha256:{digest}"


@dataclass(frozen=True)
class EmbeddingCandidateCaseResult:
    case_id: str
    unit_id: str
    expected_relevant_rule_ids: tuple[str, ...]
    required_rule_ids: tuple[str, ...]
    actual_rule_ids: tuple[str, ...]
    forbidden_hits: tuple[str, ...]


@dataclass(frozen=True)
class EmbeddingCandidateReport:
    model_id: str
    model_version: str
    dimensions: int
    index_version: str
    case_count: int
    unit_count: int
    recall_at_5: float
    precision_at_5: float
    mrr: float
    forbidden_hits: int
    results: tuple[EmbeddingCandidateCaseResult, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def render_embedding_candidate_report(report: EmbeddingCandidateReport) -> str:
    if not isinstance(report, EmbeddingCandidateReport):
        raise TypeError("report must use EmbeddingCandidateReport")
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _reembed(index: KnowledgeIndex, provider: EmbeddingProvider) -> KnowledgeIndex:
    model_id = provider.model_id
    version = provider.version
    dimensions = provider.dimensions
    if (
        not isinstance(model_id, str)
        or not model_id
        or model_id != model_id.strip()
        or not isinstance(version, str)
        or not version
        or version != version.strip()
        or not isinstance(dimensions, int)
        or isinstance(dimensions, bool)
        or dimensions < 1
    ):
        raise ValueError("Embedding candidate metadata is invalid")
    vectors = provider.embed_passages(tuple(record.retrieval_text for record in index.records))
    if not isinstance(vectors, tuple) or len(vectors) != len(index.records):
        raise ValueError("Embedding candidate returned the wrong passage count")
    if any(
        not isinstance(vector, tuple)
        or len(vector) != dimensions
        or any(not math.isfinite(value) for value in vector)
        for vector in vectors
    ):
        raise ValueError("Embedding candidate returned invalid passage vectors")
    canonical_vectors = tuple(canonical_pgvector_embedding(vector) for vector in vectors)
    return KnowledgeIndex.create(
        origin=index.origin,
        published_build_id=index.published_build_id,
        source_bundle_id=index.source_bundle_id,
        feature_config_version=index.feature_config_version,
        annotation_version=index.annotation_version,
        catalog_version=index.catalog_version,
        retrieval_version=index.retrieval_version,
        retrieval_config_fingerprint=index.retrieval_config_fingerprint,
        embedding_model=model_id,
        embedding_version=version,
        embedding_dimensions=dimensions,
        api_symbols=index.api_symbols,
        records=tuple(
            record.model_copy(update={"embedding": vector})
            for record, vector in zip(index.records, canonical_vectors, strict=True)
        ),
    )


def _request(case: RetrievalGoldenCase, index: KnowledgeIndex) -> RetrievalRequest:
    return RetrievalRequest.create(
        context_plan_id=_stable_id("context-plan", case.case_id),
        feature_routing_id=_stable_id("feature-routing", case.case_id),
        feature_config_version=index.feature_config_version,
        index_version=index.index_version,
        target_platform=case.target_platform,
        total_knowledge_token_budget=sum(unit.knowledge_token_budget for unit in case.units),
        units=case.units,
    )


def evaluate_embedding_candidate(
    manifest_path: str | Path,
    provider: EmbeddingProvider,
) -> EmbeddingCandidateReport:
    manifest, fixture_index = load_retrieval_golden_manifest(manifest_path)
    index = _reembed(fixture_index, provider)
    service = RetrievalService(
        index,
        embedding_provider=provider,
        allow_golden_fixture=True,
    )
    results: list[EmbeddingCandidateCaseResult] = []
    required_total = 0
    required_hits = 0
    precision_total = 0.0
    reciprocal_ranks: list[float] = []
    forbidden_total = 0
    hybrid_cases = tuple(case for case in manifest.cases if case.retrieval_mode == "hybrid")
    for case in hybrid_cases:
        pack = service.retrieve(_request(case, index))
        for expected, actual in zip(case.expected_units, pack.units, strict=True):
            actual_ids = tuple(clause.rule_id for clause in actual.clauses)
            first_five = set(actual_ids[:5])
            required = set(expected.required_rule_ids)
            relevant = set(expected.ordered_rule_ids)
            forbidden = tuple(sorted(set(expected.forbidden_rule_ids).intersection(actual_ids)))
            required_total += len(required)
            required_hits += len(required.intersection(first_five))
            precision_total += (
                len(relevant.intersection(first_five)) / min(5, len(actual_ids))
                if actual_ids
                else float(not relevant)
            )
            if required:
                first_rank = next(
                    (
                        rank
                        for rank, rule_id in enumerate(actual_ids, start=1)
                        if rule_id in required
                    ),
                    None,
                )
                reciprocal_ranks.append(0.0 if first_rank is None else 1 / first_rank)
            forbidden_total += len(forbidden)
            results.append(
                EmbeddingCandidateCaseResult(
                    case_id=case.case_id,
                    unit_id=expected.unit_id,
                    expected_relevant_rule_ids=expected.ordered_rule_ids,
                    required_rule_ids=expected.required_rule_ids,
                    actual_rule_ids=actual_ids,
                    forbidden_hits=forbidden,
                )
            )
    unit_count = len(results)
    return EmbeddingCandidateReport(
        model_id=provider.model_id,
        model_version=provider.version,
        dimensions=provider.dimensions,
        index_version=index.index_version,
        case_count=len(hybrid_cases),
        unit_count=unit_count,
        recall_at_5=round(required_hits / required_total, 6) if required_total else 1.0,
        precision_at_5=round(precision_total / unit_count, 6) if unit_count else 1.0,
        mrr=round(sum(reciprocal_ranks) / len(reciprocal_ranks), 6) if reciprocal_ranks else 1.0,
        forbidden_hits=forbidden_total,
        results=tuple(results),
    )


__all__ = [
    "EmbeddingCandidateCaseResult",
    "EmbeddingCandidateReport",
    "evaluate_embedding_candidate",
    "render_embedding_candidate_report",
]
