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
_MIGRATION_LOCK_ID = 4_702_978_035_688_632_935
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MIGRATION_DIRECTORY = _REPOSITORY_ROOT / "migrations" / "retrieval"

_BOOTSTRAP_STATEMENTS = (
    "CREATE SCHEMA IF NOT EXISTS retrieval",
    """
    CREATE TABLE IF NOT EXISTS retrieval.schema_migrations (
        version text PRIMARY KEY,
        filename text NOT NULL UNIQUE,
        checksum_sha256 text NOT NULL CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
        applied_at timestamptz NOT NULL DEFAULT transaction_timestamp()
    )
    """,
)
_SELECT_APPLIED = """
    SELECT version, filename, checksum_sha256
    FROM retrieval.schema_migrations
    ORDER BY version
"""
_INSERT_APPLIED = """
    INSERT INTO retrieval.schema_migrations (version, filename, checksum_sha256)
    VALUES (%s, %s, %s)
"""


class PostgresMigrationError(RuntimeError):
    """Base error for fail-closed PostgreSQL migration handling."""


class MigrationDiscoveryError(PostgresMigrationError):
    """The local migration set is missing, malformed, or ambiguous."""


class MigrationHistoryError(PostgresMigrationError):
    """The database migration history is not a prefix of the local history."""


class MigrationDriftError(PostgresMigrationError):
    """An applied migration no longer matches its recorded filename or checksum."""


class MigrationDatabaseError(PostgresMigrationError):
    """The database is unavailable or rejected a migration operation."""


class MigrationDependencyError(PostgresMigrationError):
    """The optional PostgreSQL runtime dependencies are not installed."""


@dataclass(frozen=True, slots=True)
class PostgresMigration:
    version: str
    filename: str
    checksum_sha256: str
    sql: str


@dataclass(frozen=True, slots=True)
class MigrationReport:
    applied_versions: tuple[str, ...]
    previously_applied_versions: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.applied_versions)


class _QueryResult(Protocol):
    def fetchall(self) -> Sequence[Sequence[object]]: ...


class MigrationConnection(Protocol):
    def execute(
        self,
        query: str,
        params: tuple[object, ...] | None = None,
    ) -> _QueryResult: ...

    def transaction(self) -> AbstractContextManager[object]: ...


MigrationConnector = Callable[[str], AbstractContextManager[MigrationConnection]]


def discover_postgres_migrations(
    directory: str | Path = DEFAULT_MIGRATION_DIRECTORY,
) -> tuple[PostgresMigration, ...]:
    migration_directory = Path(directory)
    if migration_directory.is_symlink() or not migration_directory.is_dir():
        raise MigrationDiscoveryError(
            f"migration directory must be a regular non-symlink directory: {migration_directory}"
        )

    migrations: list[PostgresMigration] = []
    versions: set[str] = set()
    for path in sorted(migration_directory.glob("*.sql"), key=lambda item: item.name):
        match = _MIGRATION_FILENAME.fullmatch(path.name)
        if match is None:
            raise MigrationDiscoveryError(f"invalid migration filename: {path.name}")
        if path.is_symlink() or not path.is_file():
            raise MigrationDiscoveryError(f"migration must be a regular non-symlink file: {path}")
        version = match.group("version")
        if version in versions:
            raise MigrationDiscoveryError(f"duplicate migration version: {version}")
        try:
            payload = path.read_bytes()
            sql = payload.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise MigrationDiscoveryError(f"cannot read migration {path}: {exc}") from exc
        if not sql.strip():
            raise MigrationDiscoveryError(f"migration is empty: {path.name}")
        versions.add(version)
        migrations.append(
            PostgresMigration(
                version=version,
                filename=path.name,
                checksum_sha256=hashlib.sha256(payload).hexdigest(),
                sql=sql,
            )
        )

    if not migrations:
        raise MigrationDiscoveryError(f"no SQL migrations found in {migration_directory}")
    return tuple(migrations)


@contextmanager
def _connect_with_psycopg(database_url: str) -> Iterator[MigrationConnection]:
    try:
        import psycopg
    except ImportError as exc:
        raise MigrationDependencyError(
            "PostgreSQL migrations require the 'retrieval' optional dependency"
        ) from exc

    try:
        with psycopg.connect(database_url, autocommit=False) as connection:
            yield cast(MigrationConnection, connection)
    except psycopg.Error as exc:
        raise MigrationDatabaseError(f"PostgreSQL migration failed: {exc}") from exc


def apply_postgres_migrations(
    database_url: str,
    directory: str | Path = DEFAULT_MIGRATION_DIRECTORY,
    *,
    connector: MigrationConnector | None = None,
) -> MigrationReport:
    if not isinstance(database_url, str) or not database_url.strip():
        raise ValueError("database_url must be non-empty text")

    migrations = discover_postgres_migrations(directory)
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
    except PostgresMigrationError:
        raise
    except Exception as exc:
        raise MigrationDatabaseError(f"PostgreSQL migration failed: {exc}") from exc

    return MigrationReport(
        applied_versions=tuple(applied_versions),
        previously_applied_versions=tuple(item.version for item in migrations[: len(applied)]),
    )


def _validate_applied_history(
    rows: Sequence[Sequence[object]],
    migrations: tuple[PostgresMigration, ...],
) -> tuple[PostgresMigration, ...]:
    if len(rows) > len(migrations):
        raise MigrationHistoryError("database contains more migrations than the local history")

    applied: list[PostgresMigration] = []
    for position, row in enumerate(rows):
        if len(row) != 3 or any(not isinstance(value, str) for value in row):
            raise MigrationHistoryError("database migration history contains malformed rows")
        version, filename, checksum = cast(tuple[str, str, str], tuple(row))
        expected = migrations[position]
        if version != expected.version:
            raise MigrationHistoryError(
                "database migration history is not a prefix of the local migration history"
            )
        if filename != expected.filename:
            raise MigrationDriftError(
                f"applied migration {version} filename drifted: {filename!r} != "
                f"{expected.filename!r}"
            )
        if checksum != expected.checksum_sha256:
            raise MigrationDriftError(f"applied migration {version} checksum drifted")
        applied.append(expected)
    return tuple(applied)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply checksummed Retrieval PostgreSQL migrations"
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--migration-directory", type=Path, default=DEFAULT_MIGRATION_DIRECTORY)
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    try:
        report = apply_postgres_migrations(
            args.database_url,
            args.migration_directory,
        )
    except PostgresMigrationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if report.changed:
        print("applied Retrieval migrations: " + ", ".join(report.applied_versions))
    else:
        print("Retrieval database schema is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_MIGRATION_DIRECTORY",
    "MigrationDatabaseError",
    "MigrationDependencyError",
    "MigrationDiscoveryError",
    "MigrationDriftError",
    "MigrationHistoryError",
    "MigrationReport",
    "MigrationConnection",
    "PostgresMigration",
    "PostgresMigrationError",
    "apply_postgres_migrations",
    "discover_postgres_migrations",
    "main",
]
