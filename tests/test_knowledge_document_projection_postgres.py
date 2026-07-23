from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass

import pytest

from arkts_code_reviewer.knowledge.document_first._canonical import canonical_json
from arkts_code_reviewer.knowledge.document_first.projection import (
    DocumentProjectionMappingDraft,
    DocumentProjectionRecord,
    ProjectionBindingDraft,
    build_document_projection_mapping,
    build_document_projection_record,
    compile_document_projection,
)
from arkts_code_reviewer.knowledge.document_first.projection_postgres import (
    PostgresDocumentProjectionConflictError,
    PostgresDocumentProjectionConnection,
    PostgresDocumentProjectionCorruptionError,
    PostgresDocumentProjectionDatabaseError,
    PostgresDocumentProjectionNotFoundError,
    PostgresDocumentProjectionStore,
)
from arkts_code_reviewer.knowledge.document_first.projection_postgres_migrations import (
    apply_projection_postgres_migrations,
)
from arkts_code_reviewer.knowledge.document_first.source_atoms import (
    build_source_atom_set,
    slice_source_atom_text,
)
from arkts_code_reviewer.knowledge.document_first.structure import (
    build_markdown_document_map,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceRef


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
    def connect(_: str) -> Iterator[PostgresDocumentProjectionConnection]:
        yield connection

    return connect


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _projection_record(*, body_suffix: str = "") -> DocumentProjectionRecord:
    body = (
        "# TaskPool\n"
        "\n"
        "TaskPool 提供并发任务执行能力。UNIQUE_OVERVIEW\n"
        "\n"
        "## 限制\n"
        "\n"
        "任务的 CPU 执行不能超过三分钟。UNIQUE_LIMIT\n"
        "\n"
        "- 禁止访问 AppStorage。UNIQUE_PROHIBITION\n"
        "- 需要固定线程时使用 Worker。UNIQUE_ALTERNATIVE\n"
        f"{body_suffix}"
    )
    document = NormalizedDocument(
        document_id="openharmony-docs:zh-cn/taskpool.md",
        source_ref=SourceRef(
            source_id="openharmony-docs",
            revision="a" * 40,
            relative_path="zh-cn/taskpool.md",
            anchor="document",
            authority="official_documentation",
            content_hash=_sha256_bytes(body.encode("utf-8")),
        ),
        media_type="text/markdown",
        title="TaskPool",
        heading_tree=(),
        body=body,
        language="zh-CN",
        adapter_version="test-adapter-v1",
    )
    document_map = build_markdown_document_map(document)
    atom_set = build_source_atom_set(document, document_map)
    atom_ids = tuple(atom.atom_id for atom in atom_set.atoms)
    mapping = build_document_projection_mapping(
        document,
        document_map,
        atom_set,
        DocumentProjectionMappingDraft(
            document_id=document.document_id,
            bindings=(
                ProjectionBindingDraft(
                    category_kind="overview",
                    display_title="TaskPool 文档概览",
                    subject_terms=("TaskPool", "执行能力"),
                    retrieval_aliases=("任务池", "并发任务"),
                    atom_ids=atom_ids,
                ),
                ProjectionBindingDraft(
                    category_kind="prohibition",
                    display_title="TaskPool 禁止事项",
                    subject_terms=("AppStorage",),
                    retrieval_aliases=("工作线程状态",),
                    atom_ids=(atom_ids[0],),
                ),
            ),
        ),
    )
    projection = compile_document_projection(document, document_map, atom_set, mapping)
    return build_document_projection_record(
        document,
        document_map,
        atom_set,
        mapping,
        projection,
    )


def _payload_checksum(record: DocumentProjectionRecord) -> str:
    payload = canonical_json(record.model_dump(mode="json")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _version_row(record: DocumentProjectionRecord) -> tuple[object, ...]:
    return (
        "document-projection-storage-v1",
        record.projection.projection_id,
        record.document.document_id,
        record.model_dump(mode="json"),
        record.projection.markdown,
        len(record.atom_set.atoms),
        len(record.mapping.bindings),
        _payload_checksum(record),
        "retrieval_projection_only_not_evidence",
        False,
        False,
        "mechanically_verified_projection_not_semantically_reviewed",
        "mechanically_verified",
    )


def _atom_rows(record: DocumentProjectionRecord) -> tuple[tuple[object, ...], ...]:
    unclassified = set(record.mapping.unclassified_atom_ids)
    return tuple(
        (
            atom.atom_id,
            atom.ordinal,
            atom.kind,
            list(atom.heading_path),
            atom.source_span.start_line,
            atom.source_span.end_line,
            slice_source_atom_text(record.document, atom),
            atom.text_hash,
            list(atom.required_context_atom_ids),
            atom.atom_id in unclassified,
            atom.model_dump(mode="json"),
        )
        for atom in record.atom_set.atoms
    )


def _binding_rows(record: DocumentProjectionRecord) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            binding.binding_id,
            binding.category_kind,
            binding.display_title,
            list(binding.subject_terms),
            list(binding.retrieval_aliases),
            list(binding.required_context_atom_ids),
            binding.model_dump(mode="json"),
        )
        for binding in record.mapping.bindings
    )


def _binding_atom_rows(
    record: DocumentProjectionRecord,
) -> tuple[tuple[object, ...], ...]:
    ordinals = {atom.atom_id: atom.ordinal for atom in record.atom_set.atoms}
    return tuple(
        (binding.binding_id, atom_id, ordinals[atom_id])
        for binding in record.mapping.bindings
        for atom_id in sorted(binding.atom_ids, key=ordinals.__getitem__)
    )


def _load_steps(
    record: DocumentProjectionRecord,
    *,
    version_row: Sequence[object] | None = None,
    atom_rows: Sequence[Sequence[object]] | None = None,
    binding_rows: Sequence[Sequence[object]] | None = None,
    binding_atom_rows: Sequence[Sequence[object]] | None = None,
) -> list[_Step]:
    return [
        _Step(
            "SELECT schema_version",
            one=_version_row(record) if version_row is None else version_row,
        ),
        _Step(
            "FROM document_projection.projection_atoms",
            rows=_atom_rows(record) if atom_rows is None else atom_rows,
        ),
        _Step(
            "FROM document_projection.projection_bindings",
            rows=_binding_rows(record) if binding_rows is None else binding_rows,
        ),
        _Step(
            "FROM document_projection.projection_binding_atoms",
            rows=(
                _binding_atom_rows(record)
                if binding_atom_rows is None
                else binding_atom_rows
            ),
        ),
    ]


def _first_write_steps(record: DocumentProjectionRecord) -> list[_Step]:
    steps = [
        _Step("set_config('lock_timeout'"),
        _Step("pg_advisory_xact_lock"),
        _Step("SELECT payload_sha256", one=None),
        _Step("INSERT INTO document_projection.projection_versions"),
    ]
    steps.extend(
        _Step("INSERT INTO document_projection.projection_atoms")
        for _ in record.atom_set.atoms
    )
    for binding in record.mapping.bindings:
        steps.append(_Step("INSERT INTO document_projection.projection_bindings"))
        steps.extend(
            _Step("INSERT INTO document_projection.projection_binding_atoms")
            for _ in binding.atom_ids
        )
    steps.append(
        _Step(
            "UPDATE document_projection.projection_versions",
            one=("mechanically_verified",),
        )
    )
    return [*steps, *_load_steps(record)]


def test_first_write_inserts_normalized_rows_and_round_trips() -> None:
    record = _projection_record()
    connection = _ScriptedConnection(_first_write_steps(record))
    store = PostgresDocumentProjectionStore(
        "postgresql://fixture/test",
        connector=_connector(connection),
    )

    assert store.put_verified_projection(record) is True
    assert connection.steps == []
    assert connection.transaction_context.committed is True

    atom_inserts = [
        params
        for sql, params in connection.executed
        if "INSERT INTO document_projection.projection_atoms" in sql
    ]
    binding_inserts = [
        params
        for sql, params in connection.executed
        if "INSERT INTO document_projection.projection_bindings" in sql
    ]
    binding_atom_inserts = [
        params
        for sql, params in connection.executed
        if "INSERT INTO document_projection.projection_binding_atoms" in sql
    ]
    assert len(atom_inserts) == len(record.atom_set.atoms)
    assert len(binding_inserts) == len(record.mapping.bindings)
    assert len(binding_atom_inserts) == sum(
        len(binding.atom_ids) for binding in record.mapping.bindings
    )
    assert atom_inserts[0] is not None
    assert atom_inserts[0][7] == slice_source_atom_text(
        record.document,
        record.atom_set.atoms[0],
    )


def test_repeated_identical_projection_is_idempotent() -> None:
    record = _projection_record()
    connection = _ScriptedConnection(
        [
            _Step("set_config('lock_timeout'"),
            _Step("pg_advisory_xact_lock"),
            _Step(
                "SELECT payload_sha256",
                one=(
                    _payload_checksum(record),
                    len(record.atom_set.atoms),
                    len(record.mapping.bindings),
                    "mechanically_verified",
                ),
            ),
            *_load_steps(record),
        ]
    )
    store = PostgresDocumentProjectionStore(
        "postgresql://fixture/test",
        connector=_connector(connection),
    )

    assert store.put_verified_projection(record) is False
    assert connection.steps == []
    assert connection.transaction_context.committed is True


def test_same_projection_id_with_different_metadata_is_a_conflict() -> None:
    record = _projection_record()
    connection = _ScriptedConnection(
        [
            _Step("set_config('lock_timeout'"),
            _Step("pg_advisory_xact_lock"),
            _Step(
                "SELECT payload_sha256",
                one=(
                    "0" * 64,
                    len(record.atom_set.atoms),
                    0,
                    "mechanically_verified",
                ),
            ),
        ]
    )
    store = PostgresDocumentProjectionStore(
        "postgresql://fixture/test",
        connector=_connector(connection),
    )

    with pytest.raises(
        PostgresDocumentProjectionConflictError,
        match="different metadata",
    ):
        store.put_verified_projection(record)
    assert connection.transaction_context.rolled_back is True


def test_load_round_trips_and_cross_checks_all_normalized_row_families() -> None:
    record = _projection_record()
    connection = _ScriptedConnection(
        _load_steps(record)
    )
    store = PostgresDocumentProjectionStore(
        "postgresql://fixture/test",
        connector=_connector(connection),
    )

    assert store.load_projection(record.projection.projection_id) == record
    assert connection.steps == []


@pytest.mark.parametrize("damaged_family", ("atom", "binding", "binding_atom"))
def test_load_rejects_normalized_rows_that_disagree_with_payload(
    damaged_family: str,
) -> None:
    record = _projection_record()
    atom_rows = list(_atom_rows(record))
    binding_rows = list(_binding_rows(record))
    binding_atom_rows = list(_binding_atom_rows(record))
    if damaged_family == "atom":
        damaged = list(atom_rows[0])
        damaged[6] = f"{damaged[6]}篡改"
        atom_rows[0] = tuple(damaged)
    elif damaged_family == "binding":
        damaged = list(binding_rows[0])
        damaged[2] = "被篡改的标题"
        binding_rows[0] = tuple(damaged)
    else:
        damaged = list(binding_atom_rows[0])
        damaged[2] = int(damaged[2]) + 1
        binding_atom_rows[0] = tuple(damaged)

    connection = _ScriptedConnection(
        [
            *_load_steps(
                record,
                atom_rows=atom_rows,
                binding_rows=binding_rows,
                binding_atom_rows=binding_atom_rows,
            ),
        ]
    )
    store = PostgresDocumentProjectionStore(
        "postgresql://fixture/test",
        connector=_connector(connection),
    )

    with pytest.raises(PostgresDocumentProjectionCorruptionError, match="disagrees"):
        store.load_projection(record.projection.projection_id)
    assert connection.transaction_context.rolled_back is True


def test_missing_projection_fails_closed() -> None:
    record = _projection_record()
    connection = _ScriptedConnection(
        [
            _Step("SELECT schema_version", one=None),
        ]
    )
    store = PostgresDocumentProjectionStore(
        "postgresql://fixture/test",
        connector=_connector(connection),
    )

    with pytest.raises(PostgresDocumentProjectionNotFoundError, match="not found"):
        store.load_projection(record.projection.projection_id)
    assert connection.transaction_context.rolled_back is True


def test_invalid_projection_id_is_rejected_before_database_access() -> None:
    connection = _ScriptedConnection(())
    store = PostgresDocumentProjectionStore(
        "postgresql://fixture/test",
        connector=_connector(connection),
    )

    with pytest.raises(ValueError, match="content identity"):
        store.load_projection("not-a-projection-id")
    assert connection.executed == []


def test_postgres_domain_is_checked_before_database_access() -> None:
    record = _projection_record(body_suffix="包含空字符：\x00\n")
    connection = _ScriptedConnection(())
    store = PostgresDocumentProjectionStore(
        "postgresql://fixture/test",
        connector=_connector(connection),
    )

    with pytest.raises(ValueError, match=r"U\+0000 unsupported by PostgreSQL"):
        store.put_verified_projection(record)
    assert connection.executed == []


def test_corrupt_record_payload_fails_closed() -> None:
    record = _projection_record()
    damaged_version = list(_version_row(record))
    damaged_version[3] = "{not-json"
    connection = _ScriptedConnection(
        [
            *_load_steps(record, version_row=tuple(damaged_version)),
        ]
    )
    store = PostgresDocumentProjectionStore(
        "postgresql://fixture/test",
        connector=_connector(connection),
    )

    with pytest.raises(PostgresDocumentProjectionCorruptionError, match="invalid JSON"):
        store.load_projection(record.projection.projection_id)
    assert connection.transaction_context.rolled_back is True


def test_offline_connector_and_transaction_failure_are_wrapped() -> None:
    record = _projection_record()

    @contextmanager
    def unavailable(_: str) -> Iterator[PostgresDocumentProjectionConnection]:
        raise OSError("database offline")
        yield  # pragma: no cover

    offline = PostgresDocumentProjectionStore(
        "postgresql://fixture/test",
        connector=unavailable,
    )
    with pytest.raises(PostgresDocumentProjectionDatabaseError, match="database offline"):
        offline.load_projection(record.projection.projection_id)

    class _FailedTransaction(AbstractContextManager[object]):
        def __enter__(self) -> object:
            raise RuntimeError("transaction failed")

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: object,
        ) -> None:
            del exc_type, exc_value, traceback

    class _FailedTransactionConnection:
        def execute(
            self,
            query: str,
            params: tuple[object, ...] | None = None,
        ) -> _Result:
            del query, params
            return _Result()

        def transaction(self) -> AbstractContextManager[object]:
            return _FailedTransaction()

    @contextmanager
    def failed_transaction(
        _: str,
    ) -> Iterator[PostgresDocumentProjectionConnection]:
        yield _FailedTransactionConnection()

    broken = PostgresDocumentProjectionStore(
        "postgresql://fixture/test",
        connector=failed_transaction,
    )
    with pytest.raises(PostgresDocumentProjectionDatabaseError, match="transaction failed"):
        broken.load_projection(record.projection.projection_id)


@pytest.mark.integration
def test_real_postgres_projection_round_trip_when_explicitly_configured() -> None:
    database_url = os.environ.get("ARKTS_RETRIEVAL_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set ARKTS_RETRIEVAL_TEST_DATABASE_URL to run PostgreSQL integration")

    apply_projection_postgres_migrations(database_url)
    record = _projection_record()
    store = PostgresDocumentProjectionStore(database_url)

    first_write = store.put_verified_projection(record)
    assert first_write in (True, False)
    assert store.load_projection(record.projection.projection_id) == record
    assert store.put_verified_projection(record) is False

    import psycopg

    with psycopg.connect(database_url, autocommit=False) as connection:
        with pytest.raises(psycopg.Error, match="sealed and cannot accept new rows"):
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO document_projection.projection_atoms (
                        projection_version,
                        atom_id,
                        ordinal,
                        kind,
                        heading_path,
                        start_line,
                        end_line,
                        body_text,
                        text_hash,
                        required_context_atom_ids,
                        is_unclassified,
                        atom_payload
                    ) VALUES (
                        %s, %s, %s, 'paragraph', ARRAY[]::text[], 1, 1, 'forged',
                        %s, ARRAY[]::text[], true, '{}'::jsonb
                    )
                    """,
                    (
                        record.projection.projection_id,
                        "source-atom:sha256:" + "e" * 64,
                        len(record.atom_set.atoms),
                        "sha256:" + "e" * 64,
                    ),
                )

    with psycopg.connect(database_url, autocommit=False) as connection:
        with pytest.raises(psycopg.Error, match="must start in building state"):
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO document_projection.projection_versions (
                        schema_version,
                        projection_version,
                        document_id,
                        record_payload,
                        l2_markdown,
                        atom_count,
                        binding_count,
                        payload_sha256,
                        state
                    ) VALUES (
                        'document-projection-storage-v1', %s, 'forged:document',
                        '{}'::jsonb, 'forged', 1, 0, %s, 'mechanically_verified'
                    )
                    """,
                    (
                        "document-projection:sha256:" + "e" * 64,
                        "e" * 64,
                    ),
                )

    with psycopg.connect(database_url, autocommit=False) as connection:
        with pytest.raises(psycopg.Error, match="row counts do not match"):
            with connection.transaction():
                projection_id = "document-projection:sha256:" + "d" * 64
                connection.execute(
                    """
                    INSERT INTO document_projection.projection_versions (
                        schema_version,
                        projection_version,
                        document_id,
                        record_payload,
                        l2_markdown,
                        atom_count,
                        binding_count,
                        payload_sha256,
                        state
                    ) VALUES (
                        'document-projection-storage-v1', %s, 'incomplete:document',
                        '{}'::jsonb, 'incomplete', 1, 0, %s, 'building'
                    )
                    """,
                    (projection_id, "d" * 64),
                )
                connection.execute(
                    """
                    UPDATE document_projection.projection_versions
                    SET state = 'mechanically_verified'
                    WHERE projection_version = %s
                    """,
                    (projection_id,),
                )
