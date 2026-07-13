from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol, cast

from pydantic import ValidationError

from arkts_code_reviewer.retrieval.models import KnowledgeIndex, KnowledgeIndexRecord

_INDEX_VERSION_RE = re.compile(r"knowledge-index:sha256:[0-9a-f]{64}\Z")
_STORE_LOCK_ID = 4_702_978_035_688_632_936

_SELECT_VERSION_EXISTS = """
    SELECT payload_sha256, record_count, state
    FROM retrieval.index_versions
    WHERE index_version = %s
"""
_SELECT_VERSION = """
    SELECT
        schema_version,
        index_version,
        origin,
        published_build_id,
        source_bundle_id,
        feature_config_version,
        annotation_version,
        catalog_version,
        retrieval_version,
        retrieval_config_fingerprint,
        embedding_model,
        embedding_version,
        embedding_dimensions,
        api_symbols,
        record_count,
        payload_sha256,
        state
    FROM retrieval.index_versions
    WHERE index_version = %s
"""
_INSERT_VERSION = """
    INSERT INTO retrieval.index_versions (
        schema_version,
        index_version,
        origin,
        published_build_id,
        source_bundle_id,
        feature_config_version,
        annotation_version,
        catalog_version,
        retrieval_version,
        retrieval_config_fingerprint,
        embedding_model,
        embedding_version,
        embedding_dimensions,
        api_symbols,
        record_count,
        payload_sha256,
        state
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s::jsonb, %s, %s, 'ready'
    )
"""
_INSERT_ENTRY = """
    INSERT INTO retrieval.index_entries (
        index_version,
        rule_id,
        rule_type,
        status,
        authority,
        clause_text,
        clause,
        annotation,
        domains,
        retrieval_text,
        token_count,
        heading_path,
        parent_context,
        neighbor_rule_ids,
        applicability,
        source_ref,
        func_ids,
        dimension_ids,
        tags,
        apis,
        components,
        decorators,
        raw_keywords,
        llm_keywords,
        scenario,
        embedding,
        embedding_dimensions,
        embedding_version
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
        %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
        %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s::vector, %s, %s
    )
"""
_SELECT_ENTRIES = """
    SELECT
        rule_id,
        rule_type,
        status,
        authority,
        clause_text,
        clause,
        annotation,
        domains,
        retrieval_text,
        token_count,
        heading_path,
        parent_context,
        neighbor_rule_ids,
        applicability,
        source_ref,
        func_ids,
        dimension_ids,
        tags,
        apis,
        components,
        decorators,
        raw_keywords,
        llm_keywords,
        scenario,
        embedding,
        embedding_dimensions,
        embedding_version
    FROM retrieval.index_entries
    WHERE index_version = %s
    ORDER BY rule_id
"""
_SELECT_ALIAS = """
    SELECT aliases.index_version
    FROM retrieval.current_index_aliases AS aliases
    JOIN retrieval.index_versions AS versions
      ON versions.index_version = aliases.index_version
     AND versions.state = aliases.index_state
    WHERE aliases.alias_name = %s
      AND aliases.index_state = 'ready'
"""
_UPSERT_ALIAS = """
    INSERT INTO retrieval.current_index_aliases (
        alias_name,
        index_version,
        index_state
    ) VALUES (%s, %s, 'ready')
    ON CONFLICT (alias_name) DO UPDATE SET
        index_version = EXCLUDED.index_version,
        index_state = EXCLUDED.index_state,
        switched_at = transaction_timestamp()
"""


class PostgresIndexStoreError(RuntimeError):
    """Base error for fail-closed PostgreSQL index persistence."""


class PostgresIndexDependencyError(PostgresIndexStoreError):
    """The optional PostgreSQL dependencies are unavailable."""


class PostgresIndexDatabaseError(PostgresIndexStoreError):
    """PostgreSQL is unavailable or rejected a store operation."""


class PostgresIndexNotFoundError(PostgresIndexStoreError):
    """A requested index or alias does not exist."""


class PostgresIndexConflictError(PostgresIndexStoreError):
    """An immutable index version already exists with different content."""


class PostgresIndexCorruptionError(PostgresIndexStoreError):
    """Stored rows cannot reconstruct their validated KnowledgeIndex."""


class _QueryResult(Protocol):
    def fetchone(self) -> Sequence[object] | None: ...

    def fetchall(self) -> Sequence[Sequence[object]]: ...


class PostgresIndexConnection(Protocol):
    def execute(
        self,
        query: str,
        params: tuple[object, ...] | None = None,
    ) -> _QueryResult: ...

    def transaction(self) -> AbstractContextManager[object]: ...


PostgresIndexConnector = Callable[
    [str],
    AbstractContextManager[PostgresIndexConnection],
]


@contextmanager
def _connect_with_psycopg(database_url: str) -> Iterator[PostgresIndexConnection]:
    try:
        import psycopg
        from pgvector.psycopg import register_vector
    except ImportError as exc:
        raise PostgresIndexDependencyError(
            "PostgresIndexStore requires the 'retrieval' optional dependency"
        ) from exc

    try:
        with psycopg.connect(database_url, autocommit=False) as connection:
            register_vector(connection)
            yield cast(PostgresIndexConnection, connection)
    except psycopg.Error as exc:
        raise PostgresIndexDatabaseError(f"PostgreSQL index operation failed: {exc}") from exc


class PostgresIndexStore:
    def __init__(
        self,
        database_url: str,
        *,
        connector: PostgresIndexConnector | None = None,
    ) -> None:
        if not isinstance(database_url, str) or not database_url.strip():
            raise ValueError("database_url must be non-empty text")
        self._database_url = database_url
        self._connector = _connect_with_psycopg if connector is None else connector

    def publish(self, index: KnowledgeIndex) -> bool:
        validated = _validate_index_input(index)
        expected_checksum = _payload_sha256(validated)
        with self._connection() as connection:
            with connection.transaction():
                self._lock(connection)
                existing = connection.execute(
                    _SELECT_VERSION_EXISTS,
                    (validated.index_version,),
                ).fetchone()
                if existing is not None:
                    if len(existing) != 3 or (
                        existing[0] != expected_checksum
                        or existing[1] != len(validated.records)
                        or existing[2] != "ready"
                    ):
                        raise PostgresIndexConflictError(
                            "immutable index version already exists with different metadata"
                        )
                    stored = self._load(connection, validated.index_version)
                    if stored != validated:
                        raise PostgresIndexConflictError(
                            "immutable index version already exists with different content"
                        )
                    return False

                connection.execute(
                    _INSERT_VERSION,
                    _version_parameters(validated, expected_checksum),
                )
                for record in validated.records:
                    connection.execute(
                        _INSERT_ENTRY,
                        _entry_parameters(validated, record),
                    )
                stored = self._load(connection, validated.index_version)
                if stored != validated:
                    raise PostgresIndexCorruptionError(
                        "PostgreSQL vector round-trip changed the KnowledgeIndex"
                    )
                return True

    def load(self, index_version: str) -> KnowledgeIndex:
        _validate_index_version(index_version)
        with self._connection() as connection:
            with connection.transaction():
                self._lock(connection)
                return self._load(connection, index_version)

    def resolve_alias(self, alias_name: str = "current") -> str:
        alias = _validate_alias(alias_name)
        with self._connection() as connection:
            with connection.transaction():
                self._lock(connection)
                index_version = self._resolve_alias(connection, alias)
                self._load(connection, index_version)
                return index_version

    def switch_alias(self, index_version: str, alias_name: str = "current") -> bool:
        _validate_index_version(index_version)
        alias = _validate_alias(alias_name)
        with self._connection() as connection:
            with connection.transaction():
                self._lock(connection)
                self._load(connection, index_version)
                current = connection.execute(_SELECT_ALIAS, (alias,)).fetchone()
                if current is not None and len(current) == 1 and current[0] == index_version:
                    return False
                connection.execute(_UPSERT_ALIAS, (alias, index_version))
                return True

    @contextmanager
    def _connection(self) -> Iterator[PostgresIndexConnection]:
        try:
            with self._connector(self._database_url) as connection:
                yield connection
        except PostgresIndexStoreError:
            raise
        except Exception as exc:
            raise PostgresIndexDatabaseError(f"PostgreSQL index operation failed: {exc}") from exc

    @staticmethod
    def _lock(connection: PostgresIndexConnection) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (_STORE_LOCK_ID,),
        )

    @staticmethod
    def _resolve_alias(connection: PostgresIndexConnection, alias: str) -> str:
        row = connection.execute(_SELECT_ALIAS, (alias,)).fetchone()
        if row is None:
            raise PostgresIndexNotFoundError(f"Retrieval index alias not found: {alias}")
        if len(row) != 1 or not isinstance(row[0], str):
            raise PostgresIndexCorruptionError("Retrieval index alias row is malformed")
        _validate_index_version(row[0])
        return row[0]

    @staticmethod
    def _load(connection: PostgresIndexConnection, index_version: str) -> KnowledgeIndex:
        version_row = connection.execute(_SELECT_VERSION, (index_version,)).fetchone()
        if version_row is None:
            raise PostgresIndexNotFoundError(f"Knowledge index not found: {index_version}")
        if len(version_row) != 17:
            raise PostgresIndexCorruptionError("Knowledge index version row is malformed")
        entry_rows = connection.execute(_SELECT_ENTRIES, (index_version,)).fetchall()
        return _reconstruct_index(version_row, entry_rows)


def _validate_index_input(index: KnowledgeIndex) -> KnowledgeIndex:
    if not isinstance(index, KnowledgeIndex):
        raise TypeError("index must use KnowledgeIndex")
    try:
        return KnowledgeIndex.model_validate_json(index.model_dump_json())
    except ValidationError as exc:
        raise ValueError(f"KnowledgeIndex failed validation: {exc}") from exc


def _validate_index_version(index_version: str) -> None:
    if not isinstance(index_version, str) or _INDEX_VERSION_RE.fullmatch(index_version) is None:
        raise ValueError("index_version must use the KnowledgeIndex content identity")


def _validate_alias(alias_name: str) -> str:
    if (
        not isinstance(alias_name, str)
        or not alias_name
        or alias_name != alias_name.strip()
        or any(ord(character) < 32 for character in alias_name)
    ):
        raise ValueError("alias_name must be non-empty trimmed text")
    return alias_name


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _payload_sha256(index: KnowledgeIndex) -> str:
    payload = _canonical_json(index.model_dump(mode="json")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _version_parameters(index: KnowledgeIndex, payload_sha256: str) -> tuple[object, ...]:
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
        _canonical_json([item.model_dump(mode="json") for item in index.api_symbols]),
        len(index.records),
        payload_sha256,
    )


def _entry_parameters(
    index: KnowledgeIndex,
    record: KnowledgeIndexRecord,
) -> tuple[object, ...]:
    clause = record.clause
    annotation = record.annotation
    embedding = None if record.embedding is None else _canonical_json(list(record.embedding))
    return (
        index.index_version,
        clause.rule_id,
        clause.rule_type,
        clause.status,
        clause.authority,
        clause.text,
        _canonical_json(clause.model_dump(mode="json")),
        _canonical_json(annotation.model_dump(mode="json")),
        list(record.domains),
        record.retrieval_text,
        record.token_count,
        list(clause.heading_path),
        clause.parent_context,
        list(clause.neighbor_rule_ids),
        _canonical_json(clause.applicability.model_dump(mode="json")),
        _canonical_json(clause.source_ref.model_dump(mode="json")),
        list(annotation.func_ids),
        list(annotation.dimension_ids),
        list(annotation.tags),
        list(annotation.apis),
        list(annotation.components),
        list(annotation.decorators),
        list(annotation.raw_keywords),
        list(annotation.llm_keywords),
        annotation.scenario,
        embedding,
        index.embedding_dimensions,
        index.embedding_version,
    )


def _reconstruct_index(
    version_row: Sequence[object],
    entry_rows: Sequence[Sequence[object]],
) -> KnowledgeIndex:
    (
        schema_version,
        index_version,
        origin,
        published_build_id,
        source_bundle_id,
        feature_config_version,
        annotation_version,
        catalog_version,
        retrieval_version,
        retrieval_config_fingerprint,
        embedding_model,
        embedding_version,
        embedding_dimensions,
        api_symbols,
        record_count,
        payload_sha256,
        state,
    ) = version_row
    if (
        state != "ready"
        or not isinstance(record_count, int)
        or record_count != len(entry_rows)
        or not isinstance(payload_sha256, str)
    ):
        raise PostgresIndexCorruptionError("Knowledge index readiness metadata is invalid")

    records: list[dict[str, object]] = []
    for row in entry_rows:
        if len(row) != 27:
            raise PostgresIndexCorruptionError("Knowledge index entry row is malformed")
        records.append(
            {
                "clause": _json_value(row[5], "clause"),
                "annotation": _json_value(row[6], "annotation"),
                "domains": _string_sequence(row[7], "domains"),
                "retrieval_text": row[8],
                "token_count": row[9],
                "embedding": _embedding_value(row[24]),
            }
        )
    payload = {
        "schema_version": schema_version,
        "index_version": index_version,
        "origin": origin,
        "published_build_id": published_build_id,
        "source_bundle_id": source_bundle_id,
        "feature_config_version": feature_config_version,
        "annotation_version": annotation_version,
        "catalog_version": catalog_version,
        "retrieval_version": retrieval_version,
        "retrieval_config_fingerprint": retrieval_config_fingerprint,
        "embedding_model": embedding_model,
        "embedding_version": embedding_version,
        "embedding_dimensions": embedding_dimensions,
        "api_symbols": _json_value(api_symbols, "api_symbols"),
        "records": records,
    }
    try:
        index = KnowledgeIndex.model_validate_json(_canonical_json(payload))
    except (TypeError, ValueError, ValidationError) as exc:
        raise PostgresIndexCorruptionError(
            f"stored KnowledgeIndex failed model validation: {exc}"
        ) from exc
    if _payload_sha256(index) != payload_sha256:
        raise PostgresIndexCorruptionError("stored KnowledgeIndex checksum does not match")
    for row, record in zip(entry_rows, index.records, strict=True):
        _validate_entry_projection(row, record, index)
    return index


def _validate_entry_projection(
    row: Sequence[object],
    record: KnowledgeIndexRecord,
    index: KnowledgeIndex,
) -> None:
    clause = record.clause
    annotation = record.annotation
    expected: tuple[object, ...] = (
        clause.rule_id,
        clause.rule_type,
        clause.status,
        clause.authority,
        clause.text,
        clause.model_dump(mode="json"),
        annotation.model_dump(mode="json"),
        record.domains,
        record.retrieval_text,
        record.token_count,
        clause.heading_path,
        clause.parent_context,
        clause.neighbor_rule_ids,
        clause.applicability.model_dump(mode="json"),
        clause.source_ref.model_dump(mode="json"),
        annotation.func_ids,
        annotation.dimension_ids,
        annotation.tags,
        annotation.apis,
        annotation.components,
        annotation.decorators,
        annotation.raw_keywords,
        annotation.llm_keywords,
        annotation.scenario,
        record.embedding,
        index.embedding_dimensions,
        index.embedding_version,
    )
    actual: tuple[object, ...] = (
        *row[:5],
        _json_value(row[5], "clause"),
        _json_value(row[6], "annotation"),
        _string_sequence(row[7], "domains"),
        row[8],
        row[9],
        _string_sequence(row[10], "heading_path"),
        row[11],
        _string_sequence(row[12], "neighbor_rule_ids"),
        _json_value(row[13], "applicability"),
        _json_value(row[14], "source_ref"),
        _string_sequence(row[15], "func_ids"),
        _string_sequence(row[16], "dimension_ids"),
        _string_sequence(row[17], "tags"),
        _string_sequence(row[18], "apis"),
        _string_sequence(row[19], "components"),
        _string_sequence(row[20], "decorators"),
        _string_sequence(row[21], "raw_keywords"),
        _string_sequence(row[22], "llm_keywords"),
        row[23],
        _embedding_value(row[24]),
        row[25],
        row[26],
    )
    if actual != expected:
        raise PostgresIndexCorruptionError(
            f"stored index projection disagrees with Clause {clause.rule_id}"
        )


def _json_value(value: object, context: str) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise PostgresIndexCorruptionError(f"stored {context} is invalid JSON") from exc
    if isinstance(value, dict | list):
        return value
    raise PostgresIndexCorruptionError(f"stored {context} has the wrong JSON type")


def _string_sequence(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple) or any(not isinstance(item, str) for item in value):
        raise PostgresIndexCorruptionError(f"stored {context} is not a text array")
    return tuple(value)


def _embedding_value(value: object) -> tuple[float, ...] | None:
    if value is None:
        return None
    to_list = getattr(value, "to_list", None)
    tolist = getattr(value, "tolist", None)
    if callable(to_list):
        raw = to_list()
    elif callable(tolist):
        raw = tolist()
    else:
        raw = value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PostgresIndexCorruptionError("stored embedding is malformed") from exc
    if not isinstance(raw, list | tuple):
        raise PostgresIndexCorruptionError("stored embedding is not a vector")
    try:
        return tuple(float(item) for item in raw)
    except (TypeError, ValueError) as exc:
        raise PostgresIndexCorruptionError("stored embedding contains non-numeric values") from exc


__all__ = [
    "PostgresIndexConflictError",
    "PostgresIndexConnection",
    "PostgresIndexConnector",
    "PostgresIndexCorruptionError",
    "PostgresIndexDatabaseError",
    "PostgresIndexDependencyError",
    "PostgresIndexNotFoundError",
    "PostgresIndexStore",
    "PostgresIndexStoreError",
]
