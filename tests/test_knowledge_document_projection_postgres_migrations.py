from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

import pytest

from arkts_code_reviewer.knowledge.document_first import (
    projection_postgres_migrations as migration_module,
)
from arkts_code_reviewer.knowledge.document_first.projection_postgres_migrations import (
    DEFAULT_PROJECTION_MIGRATION_DIRECTORY,
    ProjectionMigrationConnection,
    ProjectionMigrationDatabaseError,
    ProjectionMigrationDiscoveryError,
    ProjectionMigrationDriftError,
    ProjectionMigrationHistoryError,
    apply_projection_postgres_migrations,
    discover_projection_postgres_migrations,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class _FakeResult:
    def __init__(self, rows: Sequence[Sequence[object]] = ()) -> None:
        self._rows = rows

    def fetchall(self) -> Sequence[Sequence[object]]:
        return self._rows


class _FakeTransaction(AbstractContextManager[object]):
    def __init__(self) -> None:
        self.entered = False
        self.committed = False
        self.rolled_back = False

    def __enter__(self) -> object:
        self.entered = True
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


class _FakeConnection:
    def __init__(self, applied: Sequence[tuple[str, str, str]] = ()) -> None:
        self.applied = list(applied)
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []
        self.transaction_context = _FakeTransaction()

    def execute(
        self,
        query: str,
        params: tuple[object, ...] | None = None,
    ) -> _FakeResult:
        self.executed.append((query, params))
        if "SELECT version, filename, checksum_sha256" in query:
            return _FakeResult(self.applied)
        if "INSERT INTO document_projection.schema_migrations" in query:
            assert params is not None
            version, filename, checksum = params
            assert isinstance(version, str)
            assert isinstance(filename, str)
            assert isinstance(checksum, str)
            self.applied.append((version, filename, checksum))
        return _FakeResult()

    def transaction(self) -> AbstractContextManager[object]:
        return self.transaction_context


def _connector(connection: _FakeConnection):
    @contextmanager
    def connect(_: str) -> Iterator[ProjectionMigrationConnection]:
        yield connection

    return connect


def _write_migration(directory: Path, filename: str, sql: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(sql, encoding="utf-8")
    return path


def test_repository_migration_contains_shadow_storage_and_guards() -> None:
    migrations = discover_projection_postgres_migrations()

    assert [migration.version for migration in migrations] == ["0001"]
    sql = migrations[0].sql
    for expected in (
        "CREATE SCHEMA IF NOT EXISTS document_projection",
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        "document_projection.schema_migrations",
        "document_projection.projection_versions",
        "document_projection.projection_atoms",
        "document_projection.projection_bindings",
        "document_projection.projection_binding_atoms",
        "record_payload jsonb NOT NULL",
        "l2_markdown text NOT NULL",
        "retrieval_projection_only_not_evidence",
        "evidence_eligible = false",
        "production_qualified = false",
        "mechanically_verified_projection_not_semantically_reviewed",
        "state IN ('building', 'mechanically_verified')",
        "is_unclassified boolean NOT NULL",
        "reject_unclassified_binding_atom",
        "require_building_projection",
        "FOR UPDATE",
        "guard_projection_version_mutation",
        "projection_versions_start_building",
        "projection_atoms_require_building",
        "every projection Atom must be classified or explicitly unclassified",
        "FOREIGN KEY (projection_version, atom_id, atom_ordinal)",
        "body_text gin_trgm_ops",
        "display_title gin_trgm_ops",
        "projection_versions_are_immutable",
        "projection_atoms_are_immutable",
        "projection_bindings_are_immutable",
        "projection_binding_atoms_are_immutable",
    ):
        assert expected in sql
    assert "embedding vector" not in sql
    assert "current_index_aliases" not in sql


def test_default_migration_directory_is_repository_scoped() -> None:
    assert DEFAULT_PROJECTION_MIGRATION_DIRECTORY == (
        _REPOSITORY_ROOT / "migrations" / "document_projection"
    )


def test_discovery_hashes_raw_migration_bytes(tmp_path: Path) -> None:
    payload = "SELECT '二级文档';\n"
    path = _write_migration(tmp_path, "0001_initial.sql", payload)

    migration = discover_projection_postgres_migrations(tmp_path)[0]

    assert migration.filename == path.name
    assert migration.checksum_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert migration.sql == payload


def test_discovery_rejects_invalid_filename_and_empty_set(tmp_path: Path) -> None:
    with pytest.raises(ProjectionMigrationDiscoveryError, match="no SQL migrations"):
        discover_projection_postgres_migrations(tmp_path)

    _write_migration(tmp_path, "one.sql", "SELECT 1;\n")
    with pytest.raises(ProjectionMigrationDiscoveryError, match="invalid migration filename"):
        discover_projection_postgres_migrations(tmp_path)


def test_apply_uses_independent_lock_transaction_and_checksum_history(
    tmp_path: Path,
) -> None:
    _write_migration(tmp_path, "0001_initial.sql", "SELECT 1;\n")
    connection = _FakeConnection()

    report = apply_projection_postgres_migrations(
        "postgresql://local/test",
        tmp_path,
        connector=_connector(connection),
    )

    assert report.applied_versions == ("0001",)
    assert report.previously_applied_versions == ()
    assert report.changed is True
    assert connection.transaction_context.entered is True
    assert connection.transaction_context.committed is True
    assert connection.transaction_context.rolled_back is False
    assert "pg_advisory_xact_lock" in connection.executed[0][0]
    assert connection.applied[0][0:2] == ("0001", "0001_initial.sql")
    assert len(connection.applied[0][2]) == 64
    executed_sql = "\n".join(query for query, _ in connection.executed)
    assert "document_projection.schema_migrations" in executed_sql
    assert "retrieval.schema_migrations" not in executed_sql


def test_apply_is_idempotent_for_matching_history(tmp_path: Path) -> None:
    path = _write_migration(tmp_path, "0001_initial.sql", "SELECT 1;\n")
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    connection = _FakeConnection([("0001", path.name, checksum)])

    report = apply_projection_postgres_migrations(
        "postgresql://local/test",
        tmp_path,
        connector=_connector(connection),
    )

    assert report.applied_versions == ()
    assert report.previously_applied_versions == ("0001",)
    assert report.changed is False
    migration_sql_calls = [
        query for query, _ in connection.executed if query == "SELECT 1;\n"
    ]
    assert migration_sql_calls == []


def test_apply_rejects_applied_checksum_drift_and_rolls_back(tmp_path: Path) -> None:
    path = _write_migration(tmp_path, "0001_initial.sql", "SELECT 1;\n")
    connection = _FakeConnection([("0001", path.name, "0" * 64)])

    with pytest.raises(ProjectionMigrationDriftError, match="checksum drifted"):
        apply_projection_postgres_migrations(
            "postgresql://local/test",
            tmp_path,
            connector=_connector(connection),
        )

    assert connection.transaction_context.rolled_back is True


def test_apply_rejects_non_prefix_database_history(tmp_path: Path) -> None:
    first = _write_migration(tmp_path, "0001_initial.sql", "SELECT 1;\n")
    second = _write_migration(tmp_path, "0002_next.sql", "SELECT 2;\n")
    second_checksum = hashlib.sha256(second.read_bytes()).hexdigest()
    connection = _FakeConnection([("0002", second.name, second_checksum)])

    with pytest.raises(ProjectionMigrationHistoryError, match="not a prefix"):
        apply_projection_postgres_migrations(
            "postgresql://local/test",
            tmp_path,
            connector=_connector(connection),
        )

    assert first.is_file()
    assert connection.transaction_context.rolled_back is True


def test_database_unavailable_fails_closed(tmp_path: Path) -> None:
    _write_migration(tmp_path, "0001_initial.sql", "SELECT 1;\n")

    @contextmanager
    def unavailable(_: str) -> Iterator[ProjectionMigrationConnection]:
        raise OSError("database offline")
        yield  # pragma: no cover

    with pytest.raises(ProjectionMigrationDatabaseError, match="database offline"):
        apply_projection_postgres_migrations(
            "postgresql://local/test",
            tmp_path,
            connector=unavailable,
        )


def test_cli_uses_projection_specific_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: list[str] = []

    def apply(database_url: str, _: Path) -> migration_module.ProjectionMigrationReport:
        captured.append(database_url)
        return migration_module.ProjectionMigrationReport(
            applied_versions=("0001",),
            previously_applied_versions=(),
        )

    monkeypatch.setenv(
        "ARKTS_DOCUMENT_PROJECTION_DATABASE_URL",
        "postgresql://projection/db",
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://fallback/db")
    monkeypatch.setattr(migration_module, "apply_projection_postgres_migrations", apply)

    assert migration_module.main([]) == 0
    assert captured == ["postgresql://projection/db"]
    assert "applied Document Projection migrations: 0001" in capsys.readouterr().out
