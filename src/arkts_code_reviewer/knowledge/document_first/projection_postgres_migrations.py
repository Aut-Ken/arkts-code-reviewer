from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

_MIGRATION_FILENAME = re.compile(r"(?P<version>[0-9]{4})_[a-z][a-z0-9_]*[.]sql\Z")
_MIGRATION_LOCK_ID = 4_702_978_035_688_632_937
_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_SOURCE_PROJECTION_MIGRATION_DIRECTORY = (
    _REPOSITORY_ROOT / "migrations" / "document_projection"
)
_PACKAGED_PROJECTION_MIGRATION_DIRECTORY = Path(__file__).resolve().parent / "migrations"
DEFAULT_PROJECTION_MIGRATION_DIRECTORY = (
    _SOURCE_PROJECTION_MIGRATION_DIRECTORY
    if _SOURCE_PROJECTION_MIGRATION_DIRECTORY.is_dir()
    else _PACKAGED_PROJECTION_MIGRATION_DIRECTORY
)

_BOOTSTRAP_STATEMENTS = (
    "CREATE SCHEMA IF NOT EXISTS document_projection",
    """
    CREATE TABLE IF NOT EXISTS document_projection.schema_migrations (
        version text PRIMARY KEY,
        filename text NOT NULL UNIQUE,
        checksum_sha256 text NOT NULL CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
        applied_at timestamptz NOT NULL DEFAULT transaction_timestamp()
    )
    """,
)
_SELECT_APPLIED = """
    SELECT version, filename, checksum_sha256
    FROM document_projection.schema_migrations
    ORDER BY version
"""
_INSERT_APPLIED = """
    INSERT INTO document_projection.schema_migrations (
        version,
        filename,
        checksum_sha256
    ) VALUES (%s, %s, %s)
"""


class ProjectionPostgresMigrationError(RuntimeError):
    """Base error for fail-closed document projection migrations."""


class ProjectionMigrationDiscoveryError(ProjectionPostgresMigrationError):
    """The local document projection migration set is invalid."""


class ProjectionMigrationHistoryError(ProjectionPostgresMigrationError):
    """The database history is not a prefix of the local migration history."""


class ProjectionMigrationDriftError(ProjectionPostgresMigrationError):
    """An applied migration no longer matches its recorded checksum."""


class ProjectionMigrationDatabaseError(ProjectionPostgresMigrationError):
    """PostgreSQL is unavailable or rejected a migration operation."""


class ProjectionMigrationDependencyError(ProjectionPostgresMigrationError):
    """The optional PostgreSQL dependency is unavailable."""


@dataclass(frozen=True, slots=True)
class ProjectionPostgresMigration:
    version: str
    filename: str
    checksum_sha256: str
    sql: str


@dataclass(frozen=True, slots=True)
class ProjectionMigrationReport:
    applied_versions: tuple[str, ...]
    previously_applied_versions: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.applied_versions)


class _QueryResult(Protocol):
    def fetchall(self) -> Sequence[Sequence[object]]: ...


class ProjectionMigrationConnection(Protocol):
    def execute(
        self,
        query: str,
        params: tuple[object, ...] | None = None,
    ) -> _QueryResult: ...

    def transaction(self) -> AbstractContextManager[object]: ...


ProjectionMigrationConnector = Callable[
    [str],
    AbstractContextManager[ProjectionMigrationConnection],
]


def discover_projection_postgres_migrations(
    directory: str | Path = DEFAULT_PROJECTION_MIGRATION_DIRECTORY,
) -> tuple[ProjectionPostgresMigration, ...]:
    migration_directory = Path(directory)
    if migration_directory.is_symlink() or not migration_directory.is_dir():
        raise ProjectionMigrationDiscoveryError(
            "migration directory must be a regular non-symlink directory: "
            f"{migration_directory}"
        )

    migrations: list[ProjectionPostgresMigration] = []
    versions: set[str] = set()
    for path in sorted(migration_directory.glob("*.sql"), key=lambda item: item.name):
        match = _MIGRATION_FILENAME.fullmatch(path.name)
        if match is None:
            raise ProjectionMigrationDiscoveryError(
                f"invalid migration filename: {path.name}"
            )
        if path.is_symlink() or not path.is_file():
            raise ProjectionMigrationDiscoveryError(
                f"migration must be a regular non-symlink file: {path}"
            )
        version = match.group("version")
        if version in versions:
            raise ProjectionMigrationDiscoveryError(
                f"duplicate migration version: {version}"
            )
        try:
            payload = path.read_bytes()
            sql = payload.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ProjectionMigrationDiscoveryError(
                f"cannot read migration {path}: {exc}"
            ) from exc
        if not sql.strip():
            raise ProjectionMigrationDiscoveryError(f"migration is empty: {path.name}")
        versions.add(version)
        migrations.append(
            ProjectionPostgresMigration(
                version=version,
                filename=path.name,
                checksum_sha256=hashlib.sha256(payload).hexdigest(),
                sql=sql,
            )
        )

    if not migrations:
        raise ProjectionMigrationDiscoveryError(
            f"no SQL migrations found in {migration_directory}"
        )
    return tuple(migrations)


@contextmanager
def _connect_with_psycopg(
    database_url: str,
) -> Iterator[ProjectionMigrationConnection]:
    try:
        import psycopg
    except ImportError as exc:
        raise ProjectionMigrationDependencyError(
            "Document projection PostgreSQL migrations require the "
            "'retrieval' optional dependency"
        ) from exc

    try:
        with psycopg.connect(database_url, autocommit=False) as connection:
            yield cast(ProjectionMigrationConnection, connection)
    except psycopg.Error as exc:
        raise ProjectionMigrationDatabaseError(
            f"Document projection PostgreSQL migration failed: {exc}"
        ) from exc


def apply_projection_postgres_migrations(
    database_url: str,
    directory: str | Path = DEFAULT_PROJECTION_MIGRATION_DIRECTORY,
    *,
    connector: ProjectionMigrationConnector | None = None,
) -> ProjectionMigrationReport:
    if not isinstance(database_url, str) or not database_url.strip():
        raise ValueError("database_url must be non-empty text")

    migrations = discover_projection_postgres_migrations(directory)
    connect = _connect_with_psycopg if connector is None else connector
    try:
        with connect(database_url) as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (_MIGRATION_LOCK_ID,),
                )
                for statement in _BOOTSTRAP_STATEMENTS:
                    connection.execute(statement)

                applied_rows = connection.execute(_SELECT_APPLIED).fetchall()
                applied = _validate_applied_history(applied_rows, migrations)
                applied_versions: list[str] = []
                for migration in migrations[len(applied) :]:
                    connection.execute(migration.sql)
                    connection.execute(
                        _INSERT_APPLIED,
                        (
                            migration.version,
                            migration.filename,
                            migration.checksum_sha256,
                        ),
                    )
                    applied_versions.append(migration.version)
    except ProjectionPostgresMigrationError:
        raise
    except Exception as exc:
        raise ProjectionMigrationDatabaseError(
            f"Document projection PostgreSQL migration failed: {exc}"
        ) from exc

    return ProjectionMigrationReport(
        applied_versions=tuple(applied_versions),
        previously_applied_versions=tuple(
            item.version for item in migrations[: len(applied)]
        ),
    )


def _validate_applied_history(
    rows: Sequence[Sequence[object]],
    migrations: tuple[ProjectionPostgresMigration, ...],
) -> tuple[ProjectionPostgresMigration, ...]:
    if len(rows) > len(migrations):
        raise ProjectionMigrationHistoryError(
            "database contains more migrations than the local history"
        )

    applied: list[ProjectionPostgresMigration] = []
    for position, row in enumerate(rows):
        if len(row) != 3 or any(not isinstance(value, str) for value in row):
            raise ProjectionMigrationHistoryError(
                "database migration history contains malformed rows"
            )
        version, filename, checksum = cast(tuple[str, str, str], tuple(row))
        expected = migrations[position]
        if version != expected.version:
            raise ProjectionMigrationHistoryError(
                "database migration history is not a prefix of the local migration history"
            )
        if filename != expected.filename:
            raise ProjectionMigrationDriftError(
                f"applied migration {version} filename drifted: {filename!r} != "
                f"{expected.filename!r}"
            )
        if checksum != expected.checksum_sha256:
            raise ProjectionMigrationDriftError(
                f"applied migration {version} checksum drifted"
            )
        applied.append(expected)
    return tuple(applied)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply checksummed Document Projection PostgreSQL migrations"
    )
    parser.add_argument(
        "--database-url",
        default=(
            os.environ.get("ARKTS_DOCUMENT_PROJECTION_DATABASE_URL")
            or os.environ.get("DATABASE_URL")
        ),
    )
    parser.add_argument(
        "--migration-directory",
        type=Path,
        default=DEFAULT_PROJECTION_MIGRATION_DIRECTORY,
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error(
            "--database-url, ARKTS_DOCUMENT_PROJECTION_DATABASE_URL, "
            "or DATABASE_URL is required"
        )
    try:
        report = apply_projection_postgres_migrations(
            args.database_url,
            args.migration_directory,
        )
    except ProjectionPostgresMigrationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if report.changed:
        print("applied Document Projection migrations: " + ", ".join(report.applied_versions))
    else:
        print("Document Projection database schema is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_PROJECTION_MIGRATION_DIRECTORY",
    "ProjectionMigrationConnection",
    "ProjectionMigrationDatabaseError",
    "ProjectionMigrationDependencyError",
    "ProjectionMigrationDiscoveryError",
    "ProjectionMigrationDriftError",
    "ProjectionMigrationHistoryError",
    "ProjectionMigrationReport",
    "ProjectionPostgresMigration",
    "ProjectionPostgresMigrationError",
    "apply_projection_postgres_migrations",
    "discover_projection_postgres_migrations",
    "main",
]
