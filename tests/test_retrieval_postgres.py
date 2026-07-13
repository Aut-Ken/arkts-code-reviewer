from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.knowledge.models import (
    AnnotationProvenance,
    Applicability,
    KnowledgeAnnotation,
    KnowledgeClause,
    SourceRef,
    SourceSpan,
)
from arkts_code_reviewer.retrieval.config import load_default_retrieval_config
from arkts_code_reviewer.retrieval.models import KnowledgeIndex, KnowledgeIndexRecord
from arkts_code_reviewer.retrieval.postgres import (
    PostgresIndexConflictError,
    PostgresIndexConnection,
    PostgresIndexCorruptionError,
    PostgresIndexDatabaseError,
    PostgresIndexNotFoundError,
    PostgresIndexStore,
)
from arkts_code_reviewer.retrieval.postgres_migrations import (
    DEFAULT_MIGRATION_DIRECTORY,
    apply_postgres_migrations,
)


class _Result:
    def __init__(
        self,
        *,
        one: Sequence[object] | None = None,
        rows: Sequence[Sequence[object]] = (),
    ) -> None:
        self._one = one
        self._rows = rows

    def fetchone(self) -> Sequence[object] | None:
        return self._one

    def fetchall(self) -> Sequence[Sequence[object]]:
        return self._rows


@dataclass(frozen=True)
class _Step:
    marker: str
    one: Sequence[object] | None = None
    rows: Sequence[Sequence[object]] = ()


class _Transaction(AbstractContextManager[object]):
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    def __enter__(self) -> object:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_value, traceback
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None


class _ScriptedConnection:
    def __init__(self, steps: Sequence[_Step]) -> None:
        self.steps = list(steps)
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []
        self.transaction_context = _Transaction()

    def execute(
        self,
        query: str,
        params: tuple[object, ...] | None = None,
    ) -> _Result:
        normalized = " ".join(query.split())
        self.executed.append((normalized, params))
        assert self.steps, f"unexpected SQL: {normalized}"
        step = self.steps.pop(0)
        assert step.marker in normalized
        return _Result(one=step.one, rows=step.rows)

    def transaction(self) -> AbstractContextManager[object]:
        return self.transaction_context


def _connector(connection: _ScriptedConnection):
    @contextmanager
    def connect(_: str) -> Iterator[PostgresIndexConnection]:
        yield connection

    return connect


def _knowledge_index(*, embedding: bool = True) -> KnowledgeIndex:
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
        curation_version="knowledge-curation:sha256:" + "c" * 64,
        created_at=datetime(2026, 7, 13, tzinfo=UTC),
        updated_at=datetime(2026, 7, 13, tzinfo=UTC),
    )
    annotation = KnowledgeAnnotation(
        target_kind="clause",
        target_id=rule_id,
        index_version="candidate-index",
        tags=("has_timer",),
        apis=("setInterval",),
        domains=("timer-lifecycle",),
        raw_keywords=("timer",),
        provenance=(
            AnnotationProvenance(
                kind="api",
                value="setInterval",
                origin="api_catalog",
                evidence_ref="fixture:api",
            ),
            AnnotationProvenance(
                kind="domain",
                value="timer-lifecycle",
                origin="deterministic_parser",
                evidence_ref="fixture:domain",
            ),
            AnnotationProvenance(
                kind="keyword",
                value="timer",
                origin="deterministic_parser",
                evidence_ref="fixture:keyword",
            ),
            AnnotationProvenance(
                kind="tag",
                value="has_timer",
                origin="deterministic_parser",
                evidence_ref="fixture:tag",
            ),
        ),
        annotation_version="annotation-v1",
    )
    record = KnowledgeIndexRecord(
        clause=clause,
        annotation=annotation,
        domains=("timer-lifecycle",),
        retrieval_text="组件生命周期\n组件创建的定时器应在不再使用时主动清理。",
        token_count=16,
        embedding=(1.0, 0.0, 0.0) if embedding else None,
    )
    return KnowledgeIndex.create(
        origin="golden_fixture",
        published_build_id="retrieval-fixture:sha256:" + "d" * 64,
        source_bundle_id="source-bundle:sha256:" + "e" * 64,
        feature_config_version=load_default_feature_config().fingerprint,
        annotation_version="annotation-v1",
        catalog_version="api-catalog:none",
        retrieval_version="retrieval-v1",
        retrieval_config_fingerprint=load_default_retrieval_config().fingerprint,
        embedding_model="fixture-embedding" if embedding else None,
        embedding_version="fixture-embedding-v1" if embedding else None,
        embedding_dimensions=3 if embedding else None,
        api_symbols=(),
        records=(record,),
    )


def _payload_checksum(index: KnowledgeIndex) -> str:
    raw = json.dumps(
        index.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _version_row(index: KnowledgeIndex) -> tuple[object, ...]:
    return (
        index.schema_version,
        index.index_version,
        index.origin,
        index.published_build_id,
        index.source_bundle_id,
        index.feature_config_version,
        index.annotation_version,
        index.catalog_version,
        index.retrieval_version,
        index.retrieval_config_fingerprint,
        index.embedding_model,
        index.embedding_version,
        index.embedding_dimensions,
        [],
        len(index.records),
        _payload_checksum(index),
        "ready",
    )


def _entry_row(index: KnowledgeIndex) -> tuple[object, ...]:
    record = index.records[0]
    clause = record.clause
    annotation = record.annotation
    return (
        clause.rule_id,
        clause.rule_type,
        clause.status,
        clause.authority,
        clause.text,
        clause.model_dump(mode="json"),
        annotation.model_dump(mode="json"),
        list(record.domains),
        record.retrieval_text,
        record.token_count,
        list(clause.heading_path),
        clause.parent_context,
        list(clause.neighbor_rule_ids),
        clause.applicability.model_dump(mode="json"),
        clause.source_ref.model_dump(mode="json"),
        list(annotation.func_ids),
        list(annotation.dimension_ids),
        list(annotation.tags),
        list(annotation.apis),
        list(annotation.components),
        list(annotation.decorators),
        list(annotation.raw_keywords),
        list(annotation.llm_keywords),
        annotation.scenario,
        list(record.embedding) if record.embedding is not None else None,
        index.embedding_dimensions,
        index.embedding_version,
    )


def _load_steps(index: KnowledgeIndex) -> list[_Step]:
    return [
        _Step("FROM retrieval.index_versions", one=_version_row(index)),
        _Step("FROM retrieval.index_entries", rows=(_entry_row(index),)),
    ]


def test_publish_round_trips_pgvector_and_is_idempotent() -> None:
    index = _knowledge_index()
    first_steps = [
        _Step("pg_advisory_xact_lock"),
        _Step("SELECT payload_sha256", one=None),
        _Step("INSERT INTO retrieval.index_versions"),
        _Step("INSERT INTO retrieval.index_entries"),
        *_load_steps(index),
    ]
    first_connection = _ScriptedConnection(first_steps)
    store = PostgresIndexStore(
        "postgresql://fixture/test",
        connector=_connector(first_connection),
    )

    assert store.publish(index) is True
    assert first_connection.steps == []
    assert first_connection.transaction_context.committed is True
    entry_insert = next(
        params
        for sql, params in first_connection.executed
        if "INSERT INTO retrieval.index_entries" in sql
    )
    assert entry_insert is not None
    assert entry_insert[25] == "[1.0,0.0,0.0]"

    repeated_connection = _ScriptedConnection(
        [
            _Step("pg_advisory_xact_lock"),
            _Step(
                "SELECT payload_sha256",
                one=(_payload_checksum(index), 1, "ready"),
            ),
            *_load_steps(index),
        ]
    )
    repeated = PostgresIndexStore(
        "postgresql://fixture/test",
        connector=_connector(repeated_connection),
    )
    assert repeated.publish(index) is False
    assert repeated_connection.steps == []


def test_publish_rejects_immutable_version_conflict() -> None:
    index = _knowledge_index()
    connection = _ScriptedConnection(
        [
            _Step("pg_advisory_xact_lock"),
            _Step("SELECT payload_sha256", one=("0" * 64, 1, "ready")),
        ]
    )
    store = PostgresIndexStore(
        "postgresql://fixture/test",
        connector=_connector(connection),
    )

    with pytest.raises(PostgresIndexConflictError, match="different metadata"):
        store.publish(index)
    assert connection.transaction_context.rolled_back is True


def test_load_performs_full_model_and_projection_validation() -> None:
    index = _knowledge_index()
    connection = _ScriptedConnection([_Step("pg_advisory_xact_lock"), *_load_steps(index)])
    store = PostgresIndexStore(
        "postgresql://fixture/test",
        connector=_connector(connection),
    )

    assert store.load(index.index_version) == index
    assert connection.steps == []

    damaged = list(_entry_row(index))
    damaged[17] = []
    corrupted_connection = _ScriptedConnection(
        [
            _Step("pg_advisory_xact_lock"),
            _Step("FROM retrieval.index_versions", one=_version_row(index)),
            _Step("FROM retrieval.index_entries", rows=(tuple(damaged),)),
        ]
    )
    corrupted = PostgresIndexStore(
        "postgresql://fixture/test",
        connector=_connector(corrupted_connection),
    )
    with pytest.raises(PostgresIndexCorruptionError, match="projection disagrees"):
        corrupted.load(index.index_version)


def test_load_missing_index_fails_closed() -> None:
    index = _knowledge_index()
    connection = _ScriptedConnection(
        [
            _Step("pg_advisory_xact_lock"),
            _Step("FROM retrieval.index_versions", one=None),
        ]
    )
    store = PostgresIndexStore(
        "postgresql://fixture/test",
        connector=_connector(connection),
    )

    with pytest.raises(PostgresIndexNotFoundError, match="not found"):
        store.load(index.index_version)


def test_switch_and_resolve_alias_validate_ready_index() -> None:
    index = _knowledge_index(embedding=False)
    switch_connection = _ScriptedConnection(
        [
            _Step("pg_advisory_xact_lock"),
            *_load_steps(index),
            _Step("FROM retrieval.current_index_aliases", one=None),
            _Step("INSERT INTO retrieval.current_index_aliases"),
        ]
    )
    store = PostgresIndexStore(
        "postgresql://fixture/test",
        connector=_connector(switch_connection),
    )

    assert store.switch_alias(index.index_version) is True
    assert switch_connection.steps == []

    resolve_connection = _ScriptedConnection(
        [
            _Step("pg_advisory_xact_lock"),
            _Step("FROM retrieval.current_index_aliases", one=(index.index_version,)),
            *_load_steps(index),
        ]
    )
    resolver = PostgresIndexStore(
        "postgresql://fixture/test",
        connector=_connector(resolve_connection),
    )
    assert resolver.resolve_alias() == index.index_version
    assert resolve_connection.steps == []


def test_missing_alias_and_database_failure_fail_closed() -> None:
    missing_connection = _ScriptedConnection(
        [
            _Step("pg_advisory_xact_lock"),
            _Step("FROM retrieval.current_index_aliases", one=None),
        ]
    )
    missing = PostgresIndexStore(
        "postgresql://fixture/test",
        connector=_connector(missing_connection),
    )
    with pytest.raises(PostgresIndexNotFoundError, match="alias not found"):
        missing.resolve_alias()

    @contextmanager
    def unavailable(_: str) -> Iterator[PostgresIndexConnection]:
        raise OSError("database offline")
        yield  # pragma: no cover

    offline = PostgresIndexStore(
        "postgresql://fixture/test",
        connector=unavailable,
    )
    with pytest.raises(PostgresIndexDatabaseError, match="database offline"):
        offline.load(_knowledge_index().index_version)


def test_migration_only_accepts_baselined_lossless_entries() -> None:
    sql = (DEFAULT_MIGRATION_DIRECTORY / "0001_initial.sql").read_text(encoding="utf-8")

    assert "status text NOT NULL CHECK (status = 'Baselined')" in sql
    assert "clause jsonb NOT NULL" in sql
    assert "annotation jsonb NOT NULL" in sql
    assert "api_symbols jsonb NOT NULL" in sql
    assert "retrieval_config_fingerprint text NOT NULL" in sql
    assert "embedding vector" in sql
    assert "FOREIGN KEY (index_version, index_state)" in sql


@pytest.mark.integration
def test_real_postgres_publish_load_and_alias_round_trip() -> None:
    database_url = os.environ.get("ARKTS_RETRIEVAL_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set ARKTS_RETRIEVAL_TEST_DATABASE_URL to run PostgreSQL integration")

    apply_postgres_migrations(database_url)
    index = _knowledge_index()
    store = PostgresIndexStore(database_url)

    first_publish = store.publish(index)
    assert first_publish in (True, False)
    assert store.load(index.index_version) == index
    first_switch = store.switch_alias(index.index_version, "integration-current")
    assert first_switch in (True, False)
    assert store.resolve_alias("integration-current") == index.index_version
    assert store.publish(index) is False
