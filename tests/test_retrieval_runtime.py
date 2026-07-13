from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.knowledge.models import (
    AnnotationProvenance,
    Applicability,
    CurationDecision,
    KnowledgeAnnotation,
    KnowledgeClause,
    SourceRef,
    SourceSpan,
)
from arkts_code_reviewer.knowledge.publication import (
    PublishedClause,
    PublishedKnowledgeBuild,
)
from arkts_code_reviewer.retrieval import runtime
from arkts_code_reviewer.retrieval.embeddings import (
    _cache_fingerprint,
    _model_cache_fingerprint,
)
from arkts_code_reviewer.retrieval.models import KnowledgeIndex
from arkts_code_reviewer.retrieval.postgres import PostgresIndexStore
from arkts_code_reviewer.retrieval.postgres_migrations import apply_postgres_migrations
from arkts_code_reviewer.retrieval.runtime import (
    load_published_knowledge_file,
    publish_published_knowledge,
)

_NOW = datetime(2026, 7, 13, tzinfo=UTC)
_SOURCE_INDEX = "source-annotation-index-v1"
_CURATION_VERSION = "knowledge-curation:sha256:" + "c" * 64


class _FixtureEmbeddingProvider:
    model_id = "fixture-embedding"
    version = "fixture-embedding-v1"
    dimensions = 2

    def embed_passages(
        self,
        texts: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, 0.0) for _ in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        del text
        return (1.0, 0.0)


class _MemoryStore:
    def __init__(self) -> None:
        self.indexes: dict[str, KnowledgeIndex] = {}
        self.aliases: dict[str, str] = {}
        self.publish_calls = 0
        self.load_calls = 0

    def publish(self, index: KnowledgeIndex) -> bool:
        self.publish_calls += 1
        previous = self.indexes.setdefault(index.index_version, index)
        if previous != index:
            raise AssertionError("immutable fixture conflict")
        return previous is index

    def load(self, index_version: str) -> KnowledgeIndex:
        self.load_calls += 1
        return self.indexes[index_version]

    def resolve_alias(self, alias_name: str = "current") -> str:
        return self.aliases[alias_name]

    def switch_alias(self, index_version: str, alias_name: str = "current") -> bool:
        if index_version not in self.indexes:
            raise ValueError("index is not ready")
        changed = self.aliases.get(alias_name) != index_version
        self.aliases[alias_name] = index_version
        return changed


def _publication() -> PublishedKnowledgeBuild:
    rule_id = "RESOURCE/TIMER/R-01"
    source_ref = SourceRef(
        source_id="fixture",
        revision="a" * 40,
        relative_path="timer/spec.md",
        anchor="L10-L12",
        authority="official_documentation",
        content_hash="sha256:" + "b" * 64,
    )
    clause = KnowledgeClause(
        rule_id=rule_id,
        native_rule_id="R-01",
        rule_type="constraint",
        status="Baselined",
        authority=source_ref.authority,
        text="组件创建的定时器应在不再使用时主动清理。",
        heading_path=("资源管理", "定时器"),
        parent_context="组件生命周期",
        applicability=Applicability(min_api_level=12),
        source_ref=source_ref,
        source_span=SourceSpan(start_line=10, end_line=12),
        doc_hash=source_ref.content_hash,
        curation_version=_CURATION_VERSION,
        created_at=_NOW,
        updated_at=_NOW,
    )
    annotation = KnowledgeAnnotation(
        target_kind="clause",
        target_id=rule_id,
        index_version=_SOURCE_INDEX,
        domains=("timer-lifecycle",),
        provenance=(
            AnnotationProvenance(
                kind="domain",
                value="timer-lifecycle",
                origin="human_curator",
                evidence_ref="fixture:domain",
            ),
        ),
        annotation_version="annotation-v1",
    )
    published_clause = PublishedClause(
        clause=clause,
        annotation=annotation,
        domains=("timer-lifecycle",),
    )
    decision = CurationDecision(
        rule_id=rule_id,
        content_hash="sha256:" + "d" * 64,
        content_decision="approved",
        annotation_decision="approved",
        reviewer_kind="human",
        reviewer_id="fixture-human",
        review_version="fixture-review-v1",
    )
    values = {
        "packet_build_id": "knowledge-review-packets:sha256:" + "1" * 64,
        "consensus_build_id": ("knowledge-review-consensus-build:sha256:" + "2" * 64),
        "extraction_build_id": "knowledge-extraction:sha256:" + "3" * 64,
        "annotation_build_id": "knowledge-annotation:sha256:" + "4" * 64,
        "source_bundle_id": "source-bundle:sha256:" + "5" * 64,
        "feature_config_fingerprint": load_default_feature_config().fingerprint,
        "annotation_config_fingerprint": "annotation-config:sha256:" + "6" * 64,
        "annotation_version": "annotation-v1",
        "source_annotation_index_version": _SOURCE_INDEX,
        "curation_version": _CURATION_VERSION,
        "published_at": _NOW,
        "curation_decisions": (decision,),
        "clauses": (published_clause,),
        "api_symbols": (),
    }
    draft = PublishedKnowledgeBuild.model_construct(
        build_id="published-knowledge:sha256:" + "0" * 64,
        **values,
    )
    return PublishedKnowledgeBuild(build_id=draft.expected_build_id(), **values)


def test_publish_builds_only_validated_baselined_publication_and_round_trips() -> None:
    store = _MemoryStore()
    publication = _publication()

    result = publish_published_knowledge(
        publication,
        store,
        embedding_provider=_FixtureEmbeddingProvider(),
    )

    assert result.published is True
    assert result.index.origin == "publication"
    assert result.index.published_build_id == publication.build_id
    assert result.index.embedding_dimensions == 2
    assert result.index.records[0].embedding == (1.0, 0.0)
    assert result.index.records[0].clause.status == "Baselined"
    assert store.publish_calls == 1
    assert store.load_calls == 1


def test_publish_revalidates_and_rejects_model_constructed_draft() -> None:
    publication = _publication()
    original = publication.clauses[0]
    draft_clause = original.clause.model_copy(update={"status": "Draft"})
    bypassed = publication.model_copy(
        update={"clauses": (original.model_copy(update={"clause": draft_clause}),)}
    )
    store = _MemoryStore()

    with pytest.raises(ValueError, match="Baselined"):
        publish_published_knowledge(bypassed, store)
    assert store.publish_calls == 0


def test_publication_file_loader_is_strict_and_rejects_symlinks(tmp_path: Path) -> None:
    publication = _publication()
    source = tmp_path / "published.json"
    source.write_text(publication.model_dump_json(), encoding="utf-8")

    assert load_published_knowledge_file(source) == publication

    linked = tmp_path / "linked.json"
    linked.symlink_to(source)
    with pytest.raises(ValueError, match="non-symlink"):
        load_published_knowledge_file(linked)

    source.write_text('{"build_id":"one","build_id":"two"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_published_knowledge_file(source)


def test_fastembed_cache_accepts_only_managed_snapshot_symlinks(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    blobs = cache / "models--Qdrant--fixture" / "blobs"
    snapshot = cache / "models--Qdrant--fixture" / "snapshots" / "revision"
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    blob = blobs / "content-hash"
    blob.write_bytes(b"model")
    (snapshot / "model.onnx").symlink_to(Path("../../blobs/content-hash"))

    assert _cache_fingerprint(cache).startswith("sha256:")

    escaped = snapshot / "escaped.onnx"
    escaped.symlink_to(tmp_path / "outside.onnx")
    (tmp_path / "outside.onnx").write_bytes(b"outside")
    with pytest.raises(ValueError, match="local blob store"):
        _cache_fingerprint(cache)


def test_model_cache_fingerprint_ignores_unrelated_cached_models(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    model = cache / "models--Qdrant--fixture"
    blobs = model / "blobs"
    snapshot = model / "snapshots" / "revision"
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    (blobs / "content-hash").write_bytes(b"model")
    (snapshot / "model.onnx").symlink_to(Path("../../blobs/content-hash"))

    before = _model_cache_fingerprint(cache, snapshot)
    unrelated = cache / "models--Qdrant--unrelated" / "snapshots" / "other"
    unrelated.mkdir(parents=True)
    (unrelated / "model.onnx").write_bytes(b"other model")

    assert _model_cache_fingerprint(cache, snapshot) == before


def test_cli_publishes_exact_index_and_switches_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    publication = _publication()
    source = tmp_path / "published.json"
    source.write_text(publication.model_dump_json(), encoding="utf-8")
    store = _MemoryStore()
    monkeypatch.setattr(runtime, "PostgresIndexStore", lambda _: store)

    exit_code = runtime.main(
        [
            "publish",
            "--publication",
            str(source),
            "--database-url",
            "postgresql://fixture/test",
            "--exact-only",
            "--switch-alias",
            "current",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "publish"
    assert payload["published"] is True
    assert payload["embedding_model"] is None
    assert payload["alias"] == "current"
    assert store.resolve_alias() == payload["index_version"]


def test_cli_alias_commands_and_missing_database_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = _MemoryStore()
    published = publish_published_knowledge(_publication(), store)
    monkeypatch.setattr(runtime, "PostgresIndexStore", lambda _: store)

    assert (
        runtime.main(
            [
                "alias-switch",
                "--database-url",
                "postgresql://fixture/test",
                "--index-version",
                published.index.index_version,
                "--alias",
                "stable",
            ]
        )
        == 0
    )
    switched = json.loads(capsys.readouterr().out)
    assert switched["changed"] is True

    assert (
        runtime.main(
            [
                "alias-resolve",
                "--database-url",
                "postgresql://fixture/test",
                "--alias",
                "stable",
            ]
        )
        == 0
    )
    resolved = json.loads(capsys.readouterr().out)
    assert resolved["index_version"] == published.index.index_version

    monkeypatch.delenv("ARKTS_RETRIEVAL_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert runtime.main(["alias-resolve"]) == 1
    assert "DATABASE_URL is required" in capsys.readouterr().err


@pytest.mark.integration
def test_real_cli_fastembed_postgres_publish_load_and_alias_round_trip(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_url = os.environ.get("ARKTS_RETRIEVAL_TEST_DATABASE_URL")
    cache_dir = os.environ.get("ARKTS_FASTEMBED_TEST_CACHE")
    if not database_url or not cache_dir:
        pytest.skip("set ARKTS_RETRIEVAL_TEST_DATABASE_URL and ARKTS_FASTEMBED_TEST_CACHE")

    apply_postgres_migrations(database_url)
    source = tmp_path / "published.json"
    source.write_text(_publication().model_dump_json(), encoding="utf-8")
    alias = "runtime-integration-current"
    assert (
        runtime.main(
            [
                "publish",
                "--publication",
                str(source),
                "--database-url",
                database_url,
                "--embedding-cache",
                cache_dir,
                "--local-files-only",
                "--switch-alias",
                alias,
            ]
        )
        == 0
    )
    published = json.loads(capsys.readouterr().out)
    assert published["embedding_model"] == "jinaai/jina-embeddings-v2-base-code"
    assert published["embedding_dimensions"] == 768
    assert published["record_count"] == 1

    store = PostgresIndexStore(database_url)
    loaded = store.load(published["index_version"])
    assert loaded.origin == "publication"
    assert loaded.embedding_dimensions == 768
    assert len(loaded.records[0].embedding or ()) == 768
    assert loaded.records[0].clause.status == "Baselined"

    assert (
        runtime.main(
            [
                "alias-resolve",
                "--database-url",
                database_url,
                "--alias",
                alias,
            ]
        )
        == 0
    )
    resolved = json.loads(capsys.readouterr().out)
    assert resolved["index_version"] == loaded.index_version
