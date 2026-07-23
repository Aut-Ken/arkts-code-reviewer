from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol, cast

from pydantic import ValidationError

from arkts_code_reviewer.knowledge.document_first._canonical import canonical_json
from arkts_code_reviewer.knowledge.document_first.projection import (
    DOCUMENT_PROJECTION_MECHANICAL_QUALIFICATION,
    DOCUMENT_PROJECTION_USE_SCOPE,
    DocumentProjectionRecord,
    verify_document_projection_record,
)
from arkts_code_reviewer.knowledge.document_first.source_atoms import (
    slice_source_atom_text,
)

_PROJECTION_ID_RE = re.compile(r"document-projection:sha256:[0-9a-f]{64}\Z")
_STORAGE_SCHEMA_VERSION = "document-projection-storage-v1"
_LOCK_TIMEOUT = "30s"

_SELECT_VERSION_EXISTS = """
    SELECT payload_sha256, atom_count, binding_count, state
    FROM document_projection.projection_versions
    WHERE projection_version = %s
"""
_SELECT_VERSION = """
    SELECT
        schema_version,
        projection_version,
        document_id,
        record_payload,
        l2_markdown,
        atom_count,
        binding_count,
        payload_sha256,
        use_scope,
        evidence_eligible,
        production_qualified,
        qualification,
        state
    FROM document_projection.projection_versions
    WHERE projection_version = %s
"""
_INSERT_VERSION = """
    INSERT INTO document_projection.projection_versions (
        schema_version,
        projection_version,
        document_id,
        record_payload,
        l2_markdown,
        atom_count,
        binding_count,
        payload_sha256,
        use_scope,
        evidence_eligible,
        production_qualified,
        qualification,
        state
    ) VALUES (
        %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, false, false, %s,
        'building'
    )
"""
_SEAL_VERSION = """
    UPDATE document_projection.projection_versions
    SET state = 'mechanically_verified'
    WHERE projection_version = %s AND state = 'building'
    RETURNING state
"""
_INSERT_ATOM = """
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
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
    )
"""
_INSERT_BINDING = """
    INSERT INTO document_projection.projection_bindings (
        projection_version,
        binding_id,
        category_kind,
        display_title,
        subject_terms,
        retrieval_aliases,
        required_context_atom_ids,
        binding_payload
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s::jsonb
    )
"""
_INSERT_BINDING_ATOM = """
    INSERT INTO document_projection.projection_binding_atoms (
        projection_version,
        binding_id,
        atom_id,
        atom_ordinal
    ) VALUES (%s, %s, %s, %s)
"""
_SELECT_ATOMS = """
    SELECT
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
    FROM document_projection.projection_atoms
    WHERE projection_version = %s
    ORDER BY ordinal
"""
_SELECT_BINDINGS = """
    SELECT
        binding_id,
        category_kind,
        display_title,
        subject_terms,
        retrieval_aliases,
        required_context_atom_ids,
        binding_payload
    FROM document_projection.projection_bindings
    WHERE projection_version = %s
    ORDER BY binding_id COLLATE "C"
"""
_SELECT_BINDING_ATOMS = """
    SELECT binding_id, atom_id, atom_ordinal
    FROM document_projection.projection_binding_atoms
    WHERE projection_version = %s
    ORDER BY binding_id COLLATE "C", atom_ordinal
"""


class PostgresDocumentProjectionStoreError(RuntimeError):
    """Base error for fail-closed L2 projection persistence."""


class PostgresDocumentProjectionDependencyError(PostgresDocumentProjectionStoreError):
    """The optional PostgreSQL dependency is unavailable."""


class PostgresDocumentProjectionDatabaseError(PostgresDocumentProjectionStoreError):
    """PostgreSQL is unavailable or rejected an operation."""


class PostgresDocumentProjectionNotFoundError(PostgresDocumentProjectionStoreError):
    """A requested immutable projection version does not exist."""


class PostgresDocumentProjectionConflictError(PostgresDocumentProjectionStoreError):
    """An immutable projection ID already exists with different content."""


class PostgresDocumentProjectionCorruptionError(PostgresDocumentProjectionStoreError):
    """Stored JSON and query projections do not reconstruct the same artifact."""


class _QueryResult(Protocol):
    def fetchone(self) -> Sequence[object] | None: ...

    def fetchall(self) -> Sequence[Sequence[object]]: ...


class PostgresDocumentProjectionConnection(Protocol):
    def execute(
        self,
        query: str,
        params: tuple[object, ...] | None = None,
    ) -> _QueryResult: ...

    def transaction(self) -> AbstractContextManager[object]: ...


PostgresDocumentProjectionConnector = Callable[
    [str],
    AbstractContextManager[PostgresDocumentProjectionConnection],
]


@contextmanager
def _connect_with_psycopg(
    database_url: str,
) -> Iterator[PostgresDocumentProjectionConnection]:
    try:
        import psycopg
    except ImportError as exc:
        raise PostgresDocumentProjectionDependencyError(
            "PostgresDocumentProjectionStore requires the 'retrieval' optional dependency"
        ) from exc

    try:
        with psycopg.connect(database_url, autocommit=False) as connection:
            yield cast(PostgresDocumentProjectionConnection, connection)
    except psycopg.Error as exc:
        raise PostgresDocumentProjectionDatabaseError(
            f"PostgreSQL document projection operation failed: {exc}"
        ) from exc


class PostgresDocumentProjectionStore:
    def __init__(
        self,
        database_url: str,
        *,
        connector: PostgresDocumentProjectionConnector | None = None,
    ) -> None:
        if not isinstance(database_url, str) or not database_url.strip():
            raise ValueError("database_url must be non-empty text")
        self._database_url = database_url
        self._connector = _connect_with_psycopg if connector is None else connector

    def put_verified_projection(self, record: DocumentProjectionRecord) -> bool:
        validated = _validate_record_input(record)
        checksum = _payload_sha256(validated)
        projection_id = validated.projection.projection_id
        with self._connection() as connection:
            with connection.transaction():
                self._lock(connection, projection_id)
                existing = connection.execute(
                    _SELECT_VERSION_EXISTS,
                    (projection_id,),
                ).fetchone()
                if existing is not None:
                    expected = (
                        checksum,
                        len(validated.atom_set.atoms),
                        len(validated.mapping.bindings),
                        "mechanically_verified",
                    )
                    if tuple(existing) != expected:
                        raise PostgresDocumentProjectionConflictError(
                            "immutable projection version already exists with different metadata"
                        )
                    if self._load(connection, projection_id) != validated:
                        raise PostgresDocumentProjectionConflictError(
                            "immutable projection version already exists with different content"
                        )
                    return False

                connection.execute(
                    _INSERT_VERSION,
                    _version_parameters(validated, checksum),
                )
                for atom in validated.atom_set.atoms:
                    connection.execute(
                        _INSERT_ATOM,
                        _atom_parameters(validated, atom.atom_id),
                    )
                for binding in validated.mapping.bindings:
                    connection.execute(
                        _INSERT_BINDING,
                        _binding_parameters(validated, binding.binding_id),
                    )
                    for atom_id in sorted(
                        binding.atom_ids,
                        key=_atom_ordinals(validated).__getitem__,
                    ):
                        connection.execute(
                            _INSERT_BINDING_ATOM,
                            (
                                projection_id,
                                binding.binding_id,
                                atom_id,
                                _atom_ordinals(validated)[atom_id],
                            ),
                        )
                sealed = connection.execute(
                    _SEAL_VERSION,
                    (projection_id,),
                ).fetchone()
                if sealed is None or tuple(sealed) != ("mechanically_verified",):
                    raise PostgresDocumentProjectionCorruptionError(
                        "PostgreSQL did not seal the document projection"
                    )
                if self._load(connection, projection_id) != validated:
                    raise PostgresDocumentProjectionCorruptionError(
                        "PostgreSQL round-trip changed the document projection"
                    )
                return True

    def load_projection(self, projection_id: str) -> DocumentProjectionRecord:
        _validate_projection_id(projection_id)
        with self._connection() as connection:
            with connection.transaction():
                return self._load(connection, projection_id)

    @contextmanager
    def _connection(self) -> Iterator[PostgresDocumentProjectionConnection]:
        try:
            with self._connector(self._database_url) as connection:
                yield connection
        except PostgresDocumentProjectionStoreError:
            raise
        except Exception as exc:
            raise PostgresDocumentProjectionDatabaseError(
                f"PostgreSQL document projection operation failed: {exc}"
            ) from exc

    @staticmethod
    def _lock(
        connection: PostgresDocumentProjectionConnection,
        projection_id: str,
    ) -> None:
        connection.execute(
            "SELECT set_config('lock_timeout', %s, true)",
            (_LOCK_TIMEOUT,),
        )
        connection.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (_projection_lock_id(projection_id),),
        )

    @staticmethod
    def _load(
        connection: PostgresDocumentProjectionConnection,
        projection_id: str,
    ) -> DocumentProjectionRecord:
        version_row = connection.execute(_SELECT_VERSION, (projection_id,)).fetchone()
        if version_row is None:
            raise PostgresDocumentProjectionNotFoundError(
                f"document projection not found: {projection_id}"
            )
        if len(version_row) != 13:
            raise PostgresDocumentProjectionCorruptionError(
                "document projection version row is malformed"
            )
        atom_rows = connection.execute(_SELECT_ATOMS, (projection_id,)).fetchall()
        binding_rows = connection.execute(_SELECT_BINDINGS, (projection_id,)).fetchall()
        binding_atom_rows = connection.execute(
            _SELECT_BINDING_ATOMS,
            (projection_id,),
        ).fetchall()
        return _reconstruct_record(
            version_row,
            atom_rows,
            binding_rows,
            binding_atom_rows,
        )


def _validate_record_input(record: DocumentProjectionRecord) -> DocumentProjectionRecord:
    if not isinstance(record, DocumentProjectionRecord):
        raise TypeError("record must use DocumentProjectionRecord")
    try:
        validated = DocumentProjectionRecord.model_validate_json(record.model_dump_json())
        verify_document_projection_record(validated)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError(f"DocumentProjectionRecord failed validation: {exc}") from exc
    if _contains_nul(validated.model_dump(mode="json")):
        raise ValueError("DocumentProjectionRecord contains U+0000 unsupported by PostgreSQL")
    return validated


def _validate_projection_id(projection_id: str) -> None:
    if not isinstance(projection_id, str) or _PROJECTION_ID_RE.fullmatch(projection_id) is None:
        raise ValueError("projection_id must use the DocumentProjection content identity")


def _contains_nul(value: object) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_contains_nul(key) or _contains_nul(item) for key, item in value.items())
    if isinstance(value, list | tuple):
        return any(_contains_nul(item) for item in value)
    return False


def _projection_lock_id(projection_id: str) -> int:
    _validate_projection_id(projection_id)
    unsigned = int(projection_id.rsplit(":", 1)[1][:16], 16)
    return unsigned if unsigned < 2**63 else unsigned - 2**64


def _payload_sha256(record: DocumentProjectionRecord) -> str:
    payload = canonical_json(record.model_dump(mode="json")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _version_parameters(
    record: DocumentProjectionRecord,
    payload_sha256: str,
) -> tuple[object, ...]:
    return (
        _STORAGE_SCHEMA_VERSION,
        record.projection.projection_id,
        record.document.document_id,
        canonical_json(record.model_dump(mode="json")),
        record.projection.markdown,
        len(record.atom_set.atoms),
        len(record.mapping.bindings),
        payload_sha256,
        DOCUMENT_PROJECTION_USE_SCOPE,
        DOCUMENT_PROJECTION_MECHANICAL_QUALIFICATION,
    )


def _atom_ordinals(record: DocumentProjectionRecord) -> dict[str, int]:
    return {atom.atom_id: atom.ordinal for atom in record.atom_set.atoms}


def _atom_parameters(
    record: DocumentProjectionRecord,
    atom_id: str,
) -> tuple[object, ...]:
    atom = next(atom for atom in record.atom_set.atoms if atom.atom_id == atom_id)
    return (
        record.projection.projection_id,
        atom.atom_id,
        atom.ordinal,
        atom.kind,
        list(atom.heading_path),
        atom.source_span.start_line,
        atom.source_span.end_line,
        slice_source_atom_text(record.document, atom),
        atom.text_hash,
        list(atom.required_context_atom_ids),
        atom.atom_id in set(record.mapping.unclassified_atom_ids),
        canonical_json(atom.model_dump(mode="json")),
    )


def _binding_parameters(
    record: DocumentProjectionRecord,
    binding_id: str,
) -> tuple[object, ...]:
    binding = next(
        binding for binding in record.mapping.bindings if binding.binding_id == binding_id
    )
    return (
        record.projection.projection_id,
        binding.binding_id,
        binding.category_kind,
        binding.display_title,
        list(binding.subject_terms),
        list(binding.retrieval_aliases),
        list(binding.required_context_atom_ids),
        canonical_json(binding.model_dump(mode="json")),
    )


def _reconstruct_record(
    version_row: Sequence[object],
    atom_rows: Sequence[Sequence[object]],
    binding_rows: Sequence[Sequence[object]],
    binding_atom_rows: Sequence[Sequence[object]],
) -> DocumentProjectionRecord:
    (
        schema_version,
        projection_version,
        document_id,
        record_payload,
        l2_markdown,
        atom_count,
        binding_count,
        payload_sha256,
        use_scope,
        evidence_eligible,
        production_qualified,
        qualification,
        state,
    ) = version_row
    if (
        schema_version != _STORAGE_SCHEMA_VERSION
        or state != "mechanically_verified"
        or use_scope != DOCUMENT_PROJECTION_USE_SCOPE
        or evidence_eligible is not False
        or production_qualified is not False
        or qualification != DOCUMENT_PROJECTION_MECHANICAL_QUALIFICATION
        or not isinstance(atom_count, int)
        or atom_count != len(atom_rows)
        or not isinstance(binding_count, int)
        or binding_count != len(binding_rows)
        or not isinstance(payload_sha256, str)
    ):
        raise PostgresDocumentProjectionCorruptionError(
            "document projection readiness metadata is invalid"
        )

    payload = _json_object(record_payload, "record_payload")
    try:
        record = DocumentProjectionRecord.model_validate_json(canonical_json(payload))
    except (TypeError, ValueError, ValidationError) as exc:
        raise PostgresDocumentProjectionCorruptionError(
            f"stored DocumentProjectionRecord failed validation: {exc}"
        ) from exc
    if (
        record.projection.projection_id != projection_version
        or record.document.document_id != document_id
        or record.projection.markdown != l2_markdown
        or _payload_sha256(record) != payload_sha256
    ):
        raise PostgresDocumentProjectionCorruptionError(
            "document projection version row disagrees with its record payload"
        )

    _validate_atom_rows(record, atom_rows)
    _validate_binding_rows(record, binding_rows)
    _validate_binding_atom_rows(record, binding_atom_rows)
    try:
        verify_document_projection_record(record)
    except (TypeError, ValueError, ValidationError) as exc:
        raise PostgresDocumentProjectionCorruptionError(
            f"stored document projection no longer rebuilds: {exc}"
        ) from exc
    return record


def _validate_atom_rows(
    record: DocumentProjectionRecord,
    rows: Sequence[Sequence[object]],
) -> None:
    unclassified = set(record.mapping.unclassified_atom_ids)
    if len(rows) != len(record.atom_set.atoms):
        raise PostgresDocumentProjectionCorruptionError("stored Atom count is inconsistent")
    for row, atom in zip(rows, record.atom_set.atoms, strict=True):
        if len(row) != 11:
            raise PostgresDocumentProjectionCorruptionError("stored Atom row is malformed")
        expected: tuple[object, ...] = (
            atom.atom_id,
            atom.ordinal,
            atom.kind,
            atom.heading_path,
            atom.source_span.start_line,
            atom.source_span.end_line,
            slice_source_atom_text(record.document, atom),
            atom.text_hash,
            atom.required_context_atom_ids,
            atom.atom_id in unclassified,
            atom.model_dump(mode="json"),
        )
        actual: tuple[object, ...] = (
            row[0],
            row[1],
            row[2],
            _text_array(row[3], "heading_path"),
            row[4],
            row[5],
            row[6],
            row[7],
            _text_array(row[8], "required_context_atom_ids"),
            row[9],
            _json_object(row[10], "atom_payload"),
        )
        if actual != expected:
            raise PostgresDocumentProjectionCorruptionError(
                f"stored Atom projection disagrees with {atom.atom_id}"
            )


def _validate_binding_rows(
    record: DocumentProjectionRecord,
    rows: Sequence[Sequence[object]],
) -> None:
    if len(rows) != len(record.mapping.bindings):
        raise PostgresDocumentProjectionCorruptionError("stored binding count is inconsistent")
    for row, binding in zip(rows, record.mapping.bindings, strict=True):
        if len(row) != 7:
            raise PostgresDocumentProjectionCorruptionError("stored binding row is malformed")
        expected: tuple[object, ...] = (
            binding.binding_id,
            binding.category_kind,
            binding.display_title,
            binding.subject_terms,
            binding.retrieval_aliases,
            binding.required_context_atom_ids,
            binding.model_dump(mode="json"),
        )
        actual: tuple[object, ...] = (
            row[0],
            row[1],
            row[2],
            _text_array(row[3], "subject_terms"),
            _text_array(row[4], "retrieval_aliases"),
            _text_array(row[5], "required_context_atom_ids"),
            _json_object(row[6], "binding_payload"),
        )
        if actual != expected:
            raise PostgresDocumentProjectionCorruptionError(
                f"stored binding projection disagrees with {binding.binding_id}"
            )


def _validate_binding_atom_rows(
    record: DocumentProjectionRecord,
    rows: Sequence[Sequence[object]],
) -> None:
    ordinals = _atom_ordinals(record)
    expected = tuple(
        (binding.binding_id, atom_id, ordinals[atom_id])
        for binding in record.mapping.bindings
        for atom_id in sorted(binding.atom_ids, key=ordinals.__getitem__)
    )
    actual: list[tuple[object, object, object]] = []
    for row in rows:
        if len(row) != 3:
            raise PostgresDocumentProjectionCorruptionError(
                "stored binding-to-Atom row is malformed"
            )
        actual.append((row[0], row[1], row[2]))
    if tuple(actual) != expected:
        raise PostgresDocumentProjectionCorruptionError(
            "stored binding-to-Atom projection disagrees with the Mapping"
        )


def _json_object(value: object, context: str) -> dict[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PostgresDocumentProjectionCorruptionError(
                f"stored {context} is invalid JSON"
            ) from exc
    if not isinstance(value, dict):
        raise PostgresDocumentProjectionCorruptionError(
            f"stored {context} is not a JSON object"
        )
    return cast(dict[str, object], value)


def _text_array(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple) or any(not isinstance(item, str) for item in value):
        raise PostgresDocumentProjectionCorruptionError(
            f"stored {context} is not a text array"
        )
    return tuple(value)


__all__ = [
    "PostgresDocumentProjectionConflictError",
    "PostgresDocumentProjectionConnection",
    "PostgresDocumentProjectionConnector",
    "PostgresDocumentProjectionCorruptionError",
    "PostgresDocumentProjectionDatabaseError",
    "PostgresDocumentProjectionDependencyError",
    "PostgresDocumentProjectionNotFoundError",
    "PostgresDocumentProjectionStore",
    "PostgresDocumentProjectionStoreError",
]
