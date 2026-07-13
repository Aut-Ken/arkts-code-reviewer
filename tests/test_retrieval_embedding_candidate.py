from __future__ import annotations

import hashlib

import pytest

from arkts_code_reviewer.retrieval.models import KnowledgeIndex
from arkts_code_reviewer.retrieval.vector import query_embedding_text
from arkts_code_reviewer.retrieval_validation.embedding_candidate import (
    evaluate_embedding_candidate,
)
from arkts_code_reviewer.retrieval_validation.golden import (
    load_retrieval_golden_manifest,
)

MANIFEST = "tests/golden/retrieval/manifest.json"


class _ReviewedFixtureProvider:
    def __init__(self, index: KnowledgeIndex) -> None:
        self.model_id = "reviewed-fixture-provider"
        self.version = "reviewed-fixture-provider-v1"
        if index.embedding_dimensions is None:
            raise AssertionError("fixture index must contain embeddings")
        self.dimensions = index.embedding_dimensions
        self._passages = tuple(record.embedding for record in index.records)
        manifest, _ = load_retrieval_golden_manifest(MANIFEST)
        self._queries = {
            item.query_text_hash: item.vector
            for case in manifest.cases
            for item in case.query_embeddings
        }

    def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        assert len(texts) == len(self._passages)
        return tuple(value for value in self._passages if value is not None)

    def embed_query(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return self._queries[f"sha256:{digest}"]


class _WrongCountProvider(_ReviewedFixtureProvider):
    def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return super().embed_passages(texts)[:-1]


def test_reviewed_embedding_candidate_evaluator_replays_hybrid_contract() -> None:
    _, index = load_retrieval_golden_manifest(MANIFEST)

    report = evaluate_embedding_candidate(
        MANIFEST,
        _ReviewedFixtureProvider(index),
    )

    assert report.case_count == 12
    assert report.unit_count == 13
    assert report.recall_at_5 == 1.0
    assert report.precision_at_5 == 1.0
    assert report.mrr == 1.0
    assert report.forbidden_hits == 0


def test_embedding_candidate_rejects_wrong_passage_count() -> None:
    _, index = load_retrieval_golden_manifest(MANIFEST)

    with pytest.raises(ValueError, match="wrong passage count"):
        evaluate_embedding_candidate(MANIFEST, _WrongCountProvider(index))


def test_hybrid_manifest_queries_bind_current_embedding_text() -> None:
    manifest, _ = load_retrieval_golden_manifest(MANIFEST)

    assert all(
        query_embedding_text(unit)
        for case in manifest.cases
        if case.retrieval_mode == "hybrid"
        for unit in case.units
    )
