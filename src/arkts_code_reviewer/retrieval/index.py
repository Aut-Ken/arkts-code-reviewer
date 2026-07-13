from __future__ import annotations

import math
import struct
from typing import Protocol

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.knowledge.evaluation import EvaluationKnowledgeBuild
from arkts_code_reviewer.knowledge.models import Applicability
from arkts_code_reviewer.knowledge.publication import PublishedKnowledgeBuild
from arkts_code_reviewer.retrieval.catalog import aggregate_api_catalog_version
from arkts_code_reviewer.retrieval.config import load_default_retrieval_config
from arkts_code_reviewer.retrieval.models import KnowledgeIndex, KnowledgeIndexRecord


class EmbeddingProvider(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...

    def embed_query(self, text: str) -> tuple[float, ...]: ...


def estimate_knowledge_tokens(text: str) -> int:
    if not isinstance(text, str) or not text:
        raise ValueError("knowledge text must be non-empty")
    # ``UTF-8 bytes / 4`` is a useful English approximation but it
    # under-counts CJK text: one Chinese character is three UTF-8 bytes and is
    # commonly close to one model token. The budget is a safety boundary, so a
    # small over-estimate is preferable to silently overflowing the prompt.
    ascii_bytes = sum(1 for character in text if ord(character) < 128)
    non_ascii_characters = len(text) - ascii_bytes
    return max(1, math.ceil(ascii_bytes / 4) + non_ascii_characters)


def canonical_pgvector_embedding(vector: tuple[float, ...]) -> tuple[float, ...]:
    """Round an embedding to pgvector's lossless IEEE-754 float32 contract."""

    if not isinstance(vector, tuple) or not vector:
        raise ValueError("embedding must be a non-empty tuple")
    try:
        canonical = tuple(
            struct.unpack("!f", struct.pack("!f", float(value)))[0]
            for value in vector
        )
    except (OverflowError, struct.error, TypeError, ValueError) as exc:
        raise ValueError("embedding cannot be represented as pgvector float32") from exc
    if any(not math.isfinite(value) for value in canonical):
        raise ValueError("embedding must contain finite pgvector values")
    return canonical


def _embed_passages(
    texts: tuple[str, ...],
    provider: EmbeddingProvider | None,
) -> tuple[
    tuple[tuple[float, ...] | None, ...],
    str | None,
    str | None,
    int | None,
]:
    if provider is None:
        return tuple(None for _ in texts), None, None, None

    model_id = provider.model_id
    provider_version = provider.version
    dimensions = provider.dimensions
    if (
        not isinstance(model_id, str)
        or not model_id
        or model_id != model_id.strip()
        or not isinstance(provider_version, str)
        or not provider_version
        or provider_version != provider_version.strip()
        or not isinstance(dimensions, int)
        or isinstance(dimensions, bool)
        or dimensions < 1
    ):
        raise ValueError("Embedding provider metadata is invalid")
    generated = provider.embed_passages(texts)
    if not isinstance(generated, tuple):
        raise ValueError("Embedding provider must return a tuple of passage vectors")
    if len(generated) != len(texts):
        raise ValueError("Embedding provider returned the wrong passage count")
    if any(
        not isinstance(vector, tuple)
        or len(vector) != dimensions
        or any(not math.isfinite(value) for value in vector)
        for vector in generated
    ):
        raise ValueError("Embedding provider returned invalid passage vectors")
    embeddings = tuple(canonical_pgvector_embedding(vector) for vector in generated)
    return embeddings, model_id, provider_version, dimensions


def _applicability_text(value: Applicability) -> str:
    parts: list[str] = []
    if value.min_api_level is not None:
        parts.append(f"min_api_level={value.min_api_level}")
    if value.max_api_level is not None:
        parts.append(f"max_api_level={value.max_api_level}")
    for label, values in (
        ("releases", value.releases),
        ("language_modes", value.language_modes),
        ("permissions", value.permissions),
        ("system_capabilities", value.system_capabilities),
    ):
        if values:
            parts.append(f"{label}={','.join(values)}")
    return "; ".join(parts)


def retrieval_text(
    *,
    scenario: str | None,
    heading_path: tuple[str, ...],
    parent_context: str | None,
    applicability: Applicability,
    text: str,
) -> str:
    parts = [
        *(value for value in (scenario, " / ".join(heading_path)) if value),
        *(
            (f"applicability: {_applicability_text(applicability)}",)
            if _applicability_text(applicability)
            else ()
        ),
        *(value for value in (parent_context, text) if value),
    ]
    return "\n".join(parts).strip()


def build_knowledge_index(
    publication: PublishedKnowledgeBuild,
    *,
    retrieval_version: str,
    embedding_provider: EmbeddingProvider | None = None,
) -> KnowledgeIndex:
    if not isinstance(publication, PublishedKnowledgeBuild):
        raise TypeError("publication must use PublishedKnowledgeBuild")
    if not retrieval_version or retrieval_version != retrieval_version.strip():
        raise ValueError("retrieval_version must be non-empty and trimmed")
    feature_config = load_default_feature_config()
    retrieval_config = load_default_retrieval_config()
    if retrieval_version != retrieval_config.version:
        raise ValueError("retrieval_version does not match the active Retrieval config")
    if publication.feature_config_fingerprint != feature_config.fingerprint:
        raise ValueError("Published Knowledge feature config does not match runtime")

    texts = tuple(
        retrieval_text(
            scenario=item.annotation.scenario,
            heading_path=item.clause.heading_path,
            parent_context=item.clause.parent_context,
            applicability=item.clause.applicability,
            text=item.clause.text,
        )
        for item in publication.clauses
    )
    (
        embeddings,
        embedding_model,
        embedding_version,
        embedding_dimensions,
    ) = _embed_passages(texts, embedding_provider)

    records = tuple(
        KnowledgeIndexRecord(
            clause=item.clause,
            annotation=item.annotation,
            domains=item.domains,
            retrieval_text=text_value,
            token_count=estimate_knowledge_tokens(text_value),
            embedding=embedding,
        )
        for item, text_value, embedding in zip(
            publication.clauses,
            texts,
            embeddings,
            strict=True,
        )
    )
    catalog_version = aggregate_api_catalog_version(publication.api_symbols)
    return KnowledgeIndex.create(
        origin="publication",
        published_build_id=publication.build_id,
        source_bundle_id=publication.source_bundle_id,
        feature_config_version=publication.feature_config_fingerprint,
        annotation_version=publication.annotation_version,
        catalog_version=catalog_version,
        retrieval_version=retrieval_version,
        retrieval_config_fingerprint=retrieval_config.fingerprint,
        embedding_model=embedding_model,
        embedding_version=embedding_version,
        embedding_dimensions=embedding_dimensions,
        api_symbols=publication.api_symbols,
        records=records,
    )


def build_evaluation_knowledge_index(
    evaluation: EvaluationKnowledgeBuild,
    *,
    retrieval_version: str,
    embedding_provider: EmbeddingProvider | None = None,
) -> KnowledgeIndex:
    if not isinstance(evaluation, EvaluationKnowledgeBuild):
        raise TypeError("evaluation must use EvaluationKnowledgeBuild")
    if evaluation.production_eligible is not False:
        raise ValueError("Evaluation Knowledge must remain production-ineligible")
    if not retrieval_version or retrieval_version != retrieval_version.strip():
        raise ValueError("retrieval_version must be non-empty and trimmed")
    feature_config = load_default_feature_config()
    retrieval_config = load_default_retrieval_config()
    if retrieval_version != retrieval_config.version:
        raise ValueError("retrieval_version does not match the active Retrieval config")
    if evaluation.feature_config_fingerprint != feature_config.fingerprint:
        raise ValueError("Evaluation Knowledge feature config does not match runtime")

    texts = tuple(
        retrieval_text(
            scenario=item.annotation.scenario,
            heading_path=item.clause.heading_path,
            parent_context=item.clause.parent_context,
            applicability=item.clause.applicability,
            text=item.clause.text,
        )
        for item in evaluation.clauses
    )
    (
        embeddings,
        embedding_model,
        embedding_version,
        embedding_dimensions,
    ) = _embed_passages(texts, embedding_provider)

    records = tuple(
        KnowledgeIndexRecord(
            clause=item.clause,
            annotation=item.annotation,
            domains=item.domains,
            retrieval_text=text_value,
            token_count=estimate_knowledge_tokens(text_value),
            embedding=embedding,
        )
        for item, text_value, embedding in zip(
            evaluation.clauses,
            texts,
            embeddings,
            strict=True,
        )
    )
    return KnowledgeIndex.create(
        origin="evaluation_fixture",
        published_build_id=evaluation.build_id,
        source_bundle_id=evaluation.source_bundle_id,
        feature_config_version=evaluation.feature_config_fingerprint,
        annotation_version=evaluation.annotation_version,
        catalog_version=aggregate_api_catalog_version(evaluation.api_symbols),
        retrieval_version=retrieval_version,
        retrieval_config_fingerprint=retrieval_config.fingerprint,
        embedding_model=embedding_model,
        embedding_version=embedding_version,
        embedding_dimensions=embedding_dimensions,
        api_symbols=evaluation.api_symbols,
        records=records,
    )


__all__ = [
    "EmbeddingProvider",
    "build_evaluation_knowledge_index",
    "build_knowledge_index",
    "canonical_pgvector_embedding",
    "estimate_knowledge_tokens",
    "retrieval_text",
]
