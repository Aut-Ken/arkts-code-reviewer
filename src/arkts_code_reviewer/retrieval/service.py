from __future__ import annotations

from arkts_code_reviewer.retrieval.assembler import assemble_unit_evidence
from arkts_code_reviewer.retrieval.config import (
    RetrievalConfig,
    load_default_retrieval_config,
)
from arkts_code_reviewer.retrieval.exact import search_exact
from arkts_code_reviewer.retrieval.fusion import fuse_hits
from arkts_code_reviewer.retrieval.index import EmbeddingProvider
from arkts_code_reviewer.retrieval.models import (
    EvidencePack,
    KnowledgeIndex,
    RetrievalDiagnostic,
    RetrievalRequest,
)
from arkts_code_reviewer.retrieval.vector import VectorHit, search_vector


class RetrievalService:
    def __init__(
        self,
        index: KnowledgeIndex,
        *,
        config: RetrievalConfig | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        allow_golden_fixture: bool = False,
        allow_evaluation_fixture: bool = False,
    ) -> None:
        if not isinstance(index, KnowledgeIndex):
            raise TypeError("index must use KnowledgeIndex")
        if config is not None and not isinstance(config, RetrievalConfig):
            raise TypeError("config must use RetrievalConfig")
        if not isinstance(allow_golden_fixture, bool):
            raise TypeError("allow_golden_fixture must be boolean")
        if not isinstance(allow_evaluation_fixture, bool):
            raise TypeError("allow_evaluation_fixture must be boolean")
        if index.origin == "golden_fixture" and not allow_golden_fixture:
            raise ValueError("Golden fixture index requires an explicit test-only opt-in")
        if index.origin == "evaluation_fixture" and not allow_evaluation_fixture:
            raise ValueError("Evaluation fixture index requires an explicit staging opt-in")
        self.index = index
        self.config = config or load_default_retrieval_config()
        self.embedding_provider = embedding_provider

    def retrieve(self, request: RetrievalRequest) -> EvidencePack:
        if not isinstance(request, RetrievalRequest):
            raise TypeError("request must use RetrievalRequest")
        if request.index_version != self.index.index_version:
            raise ValueError("Retrieval request references a different index version")
        if request.feature_config_version != self.index.feature_config_version:
            raise ValueError("Retrieval request and index feature configs disagree")
        if (
            self.index.retrieval_version != self.config.version
            or self.index.retrieval_config_fingerprint != self.config.fingerprint
        ):
            raise ValueError("Knowledge index and Retrieval config disagree")
        if len(request.units) > self.config.max_units:
            raise ValueError("Retrieval request exceeds configured Unit limit")

        unit_evidence = []
        for unit in request.units:
            exact_hits = search_exact(
                self.index,
                unit,
                request.target_platform,
                self.config,
            )
            vector_hits: tuple[VectorHit, ...] = ()
            path_diagnostics: list[RetrievalDiagnostic] = []
            if self.index.embedding_model is not None:
                if self.embedding_provider is None:
                    path_diagnostics.append(
                        RetrievalDiagnostic(
                            code="embedding_unavailable",
                            unit_id=unit.unit_id,
                            detail=(
                                "Vector index is present but no embedding provider "
                                "is available."
                            ),
                        )
                    )
                else:
                    try:
                        vector_hits = search_vector(
                            self.index,
                            unit,
                            request.target_platform,
                            self.config,
                            self.embedding_provider,
                        )
                    except Exception as exc:
                        path_diagnostics.append(
                            RetrievalDiagnostic(
                                code="embedding_unavailable",
                                unit_id=unit.unit_id,
                                detail=(
                                    "Vector retrieval failed; exact retrieval continued: "
                                    f"{type(exc).__name__}."
                                ),
                            )
                        )
            fused = fuse_hits(exact_hits, vector_hits, rrf_k=self.config.rrf_k)
            unit_evidence.append(
                assemble_unit_evidence(
                    unit,
                    fused,
                    self.config,
                    path_diagnostics=tuple(path_diagnostics),
                )
            )
        return EvidencePack.create(
            request_id=request.request_id,
            retrieval_version=self.index.retrieval_version,
            retrieval_config_fingerprint=self.config.fingerprint,
            index_version=self.index.index_version,
            index_origin=self.index.origin,
            knowledge_build_id=self.index.published_build_id,
            production_eligible=self.index.origin == "publication",
            source_bundle_id=self.index.source_bundle_id,
            embedding_version=self.index.embedding_version,
            units=tuple(unit_evidence),
            diagnostics=(),
        )


__all__ = ["RetrievalService"]
