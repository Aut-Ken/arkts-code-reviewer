from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal, cast

from arkts_code_reviewer.code_analysis.file_analysis_models import (
    CodeSourceRef,
    ExactRange,
)
from arkts_code_reviewer.code_analysis.models import ReviewUnitSpan
from arkts_code_reviewer.code_analysis.review_unit_contract import (
    normalize_review_path,
)

CHANGE_SET_SCHEMA_VERSION = "change-set-v1"
DIFF_NORMALIZER_VERSION = "change-normalizer-v1"

ChangeAtomKind = Literal["addition", "deletion", "replacement"]
ChangedFileStatus = Literal["added", "modified", "deleted", "renamed"]
DiffSide = Literal["base", "head"]
ChangeSetDiagnosticCode = Literal["binary_source_unavailable"]

_ATOM_KINDS = {"addition", "deletion", "replacement"}
_FILE_STATUSES = {"added", "modified", "deleted", "renamed"}
_DIFF_SIDES = {"base", "head"}
_DIAGNOSTIC_CODES = {"binary_source_unavailable"}


def _stable_id(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(encoded).hexdigest()}"


def _require_text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{context} must not contain control characters")
    return value


def _require_sorted_lines(values: tuple[int, ...], context: str) -> None:
    if not isinstance(values, tuple) or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 1
        for value in values
    ):
        raise ValueError(f"{context} must contain 1-based integer lines")
    if list(values) != sorted(set(values)):
        raise ValueError(f"{context} must be sorted and unique")


def _span_payload(span: ExactRange | None) -> dict[str, int] | None:
    return None if span is None else asdict(span)


def _line_count(content: str) -> int:
    return len(content.splitlines(keepends=True))


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _exact_line_range(snapshot: CodeSourceSnapshot, span: ReviewUnitSpan) -> ExactRange:
    lines = snapshot.content.splitlines(keepends=True)
    if span.end_line > len(lines):
        raise ValueError(
            f"line span L{span.start_line}-L{span.end_line} exceeds "
            f"{snapshot.source_ref.path!r} line count {len(lines)}"
        )
    start_offset = sum(_utf16_length(line) for line in lines[: span.start_line - 1])
    end_offset = start_offset + sum(
        _utf16_length(line)
        for line in lines[span.start_line - 1 : span.end_line]
    )
    return ExactRange(
        start_line=span.start_line,
        end_line=span.end_line,
        start_offset_utf16=start_offset,
        end_offset_utf16=end_offset,
    )


@dataclass(frozen=True)
class CodeSourceSnapshot:
    """A validated source body used only while normalizing a ChangeSet."""

    source_ref: CodeSourceRef
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_ref, CodeSourceRef):
            raise ValueError("CodeSourceSnapshot.source_ref must use CodeSourceRef")
        if not isinstance(self.content, str):
            raise ValueError("CodeSourceSnapshot.content must be a string")
        self.source_ref.verify_content(self.content)


@dataclass(frozen=True)
class DiffPosition:
    side: DiffSide
    source_line: int
    diff_position: int

    def __post_init__(self) -> None:
        if self.side not in _DIFF_SIDES:
            raise ValueError(f"unsupported diff side: {self.side}")
        for value, context in (
            (self.source_line, "DiffPosition.source_line"),
            (self.diff_position, "DiffPosition.diff_position"),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{context} must be an integer >= 1")


@dataclass(frozen=True)
class ChangeAtomInput:
    """Structured diff input; it is not a raw Git hunk or parser."""

    kind: ChangeAtomKind
    old_span: ReviewUnitSpan | None
    new_span: ReviewUnitSpan | None
    added_new_lines: tuple[int, ...] = ()
    deleted_old_lines: tuple[int, ...] = ()
    diff_positions: tuple[DiffPosition, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in _ATOM_KINDS:
            raise ValueError(f"unsupported ChangeAtom kind: {self.kind}")
        for value, context in (
            (self.old_span, "ChangeAtomInput.old_span"),
            (self.new_span, "ChangeAtomInput.new_span"),
        ):
            if value is not None and not isinstance(value, ReviewUnitSpan):
                raise ValueError(f"{context} must use ReviewUnitSpan or None")
        _require_sorted_lines(
            self.added_new_lines,
            "ChangeAtomInput.added_new_lines",
        )
        _require_sorted_lines(
            self.deleted_old_lines,
            "ChangeAtomInput.deleted_old_lines",
        )
        if not isinstance(self.diff_positions, tuple) or any(
            not isinstance(item, DiffPosition) for item in self.diff_positions
        ):
            raise ValueError(
                "ChangeAtomInput.diff_positions must contain DiffPosition values"
            )
        position_keys = [
            (item.diff_position, item.side, item.source_line)
            for item in self.diff_positions
        ]
        if (
            position_keys != sorted(set(position_keys))
            or len({item.diff_position for item in self.diff_positions})
            != len(self.diff_positions)
            or len({(item.side, item.source_line) for item in self.diff_positions})
            != len(self.diff_positions)
        ):
            raise ValueError(
                "ChangeAtomInput.diff_positions must be sorted and unique"
            )


@dataclass(frozen=True)
class ChangedFileInput:
    status: ChangedFileStatus
    old_path: str | None
    new_path: str | None
    old_snapshot: CodeSourceSnapshot | None = None
    new_snapshot: CodeSourceSnapshot | None = None
    atoms: tuple[ChangeAtomInput, ...] = ()
    is_binary: bool = False

    def __post_init__(self) -> None:
        if self.status not in _FILE_STATUSES:
            raise ValueError(f"unsupported ChangedFile status: {self.status}")
        if self.old_path is not None and self.old_path != normalize_review_path(
            self.old_path
        ):
            raise ValueError("ChangedFileInput.old_path must be normalized")
        if self.new_path is not None and self.new_path != normalize_review_path(
            self.new_path
        ):
            raise ValueError("ChangedFileInput.new_path must be normalized")
        for value, context in (
            (self.old_snapshot, "ChangedFileInput.old_snapshot"),
            (self.new_snapshot, "ChangedFileInput.new_snapshot"),
        ):
            if value is not None and not isinstance(value, CodeSourceSnapshot):
                raise ValueError(f"{context} must use CodeSourceSnapshot or None")
        if not isinstance(self.atoms, tuple) or any(
            not isinstance(atom, ChangeAtomInput) for atom in self.atoms
        ):
            raise ValueError("ChangedFileInput.atoms must contain ChangeAtomInput values")
        if not isinstance(self.is_binary, bool):
            raise ValueError("ChangedFileInput.is_binary must be a boolean")


@dataclass(frozen=True)
class ChangeAtom:
    atom_id: str
    kind: ChangeAtomKind
    old_source_ref_id: str | None
    new_source_ref_id: str | None
    old_span: ExactRange | None
    new_span: ExactRange | None
    added_new_lines: tuple[int, ...]
    deleted_old_lines: tuple[int, ...]
    diff_positions: tuple[DiffPosition, ...]
    diff_normalizer_version: str

    @classmethod
    def create(
        cls,
        *,
        kind: ChangeAtomKind,
        old_source_ref_id: str | None,
        new_source_ref_id: str | None,
        old_span: ExactRange | None,
        new_span: ExactRange | None,
        added_new_lines: tuple[int, ...],
        deleted_old_lines: tuple[int, ...],
        diff_positions: tuple[DiffPosition, ...],
        diff_normalizer_version: str,
    ) -> ChangeAtom:
        payload = cls.identity_payload(
            kind=kind,
            old_source_ref_id=old_source_ref_id,
            new_source_ref_id=new_source_ref_id,
            old_span=old_span,
            new_span=new_span,
            added_new_lines=added_new_lines,
            deleted_old_lines=deleted_old_lines,
            diff_positions=diff_positions,
            diff_normalizer_version=diff_normalizer_version,
        )
        return cls(
            atom_id=_stable_id("change-atom", payload),
            kind=kind,
            old_source_ref_id=old_source_ref_id,
            new_source_ref_id=new_source_ref_id,
            old_span=old_span,
            new_span=new_span,
            added_new_lines=added_new_lines,
            deleted_old_lines=deleted_old_lines,
            diff_positions=diff_positions,
            diff_normalizer_version=diff_normalizer_version,
        )

    @staticmethod
    def identity_payload(
        *,
        kind: ChangeAtomKind,
        old_source_ref_id: str | None,
        new_source_ref_id: str | None,
        old_span: ExactRange | None,
        new_span: ExactRange | None,
        added_new_lines: tuple[int, ...],
        deleted_old_lines: tuple[int, ...],
        diff_positions: tuple[DiffPosition, ...],
        diff_normalizer_version: str,
    ) -> dict[str, object]:
        return {
            "diff_normalizer_version": diff_normalizer_version,
            "kind": kind,
            "old_source_ref_id": old_source_ref_id,
            "new_source_ref_id": new_source_ref_id,
            "old_span": _span_payload(old_span),
            "new_span": _span_payload(new_span),
            "added_new_lines": list(added_new_lines),
            "deleted_old_lines": list(deleted_old_lines),
            "diff_positions": [asdict(item) for item in diff_positions],
        }

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.kind not in _ATOM_KINDS:
            raise ValueError(f"unsupported ChangeAtom kind: {self.kind}")
        _require_text(
            self.diff_normalizer_version,
            "ChangeAtom.diff_normalizer_version",
        )
        for source_id, context in (
            (self.old_source_ref_id, "ChangeAtom.old_source_ref_id"),
            (self.new_source_ref_id, "ChangeAtom.new_source_ref_id"),
        ):
            if source_id is not None:
                _require_text(source_id, context)
        for span, context in (
            (self.old_span, "ChangeAtom.old_span"),
            (self.new_span, "ChangeAtom.new_span"),
        ):
            if span is not None and not isinstance(span, ExactRange):
                raise ValueError(f"{context} must use ExactRange or None")
        _require_sorted_lines(self.added_new_lines, "ChangeAtom.added_new_lines")
        _require_sorted_lines(self.deleted_old_lines, "ChangeAtom.deleted_old_lines")
        if not isinstance(self.diff_positions, tuple) or any(
            not isinstance(item, DiffPosition) for item in self.diff_positions
        ):
            raise ValueError("ChangeAtom.diff_positions must contain DiffPosition values")
        position_keys = [
            (item.diff_position, item.side, item.source_line)
            for item in self.diff_positions
        ]
        if (
            position_keys != sorted(set(position_keys))
            or len({item.diff_position for item in self.diff_positions})
            != len(self.diff_positions)
            or len({(item.side, item.source_line) for item in self.diff_positions})
            != len(self.diff_positions)
        ):
            raise ValueError("ChangeAtom.diff_positions must be sorted and unique")

        if self.kind == "addition":
            if (
                self.old_source_ref_id is not None
                or self.old_span is not None
                or self.deleted_old_lines
                or self.new_source_ref_id is None
                or self.new_span is None
                or not self.added_new_lines
            ):
                raise ValueError("addition ChangeAtom must describe only added head lines")
        elif self.kind == "deletion":
            if (
                self.new_source_ref_id is not None
                or self.new_span is not None
                or self.added_new_lines
                or self.old_source_ref_id is None
                or self.old_span is None
                or not self.deleted_old_lines
            ):
                raise ValueError("deletion ChangeAtom must describe only deleted base lines")
        elif (
            self.old_source_ref_id is None
            or self.new_source_ref_id is None
            or self.old_span is None
            or self.new_span is None
            or not self.added_new_lines
            or not self.deleted_old_lines
        ):
            raise ValueError("replacement ChangeAtom requires non-empty base and head sides")

        if self.old_span is not None and any(
            not (self.old_span.start_line <= line <= self.old_span.end_line)
            for line in self.deleted_old_lines
        ):
            raise ValueError("ChangeAtom deleted lines must be inside old_span")
        if self.new_span is not None and any(
            not (self.new_span.start_line <= line <= self.new_span.end_line)
            for line in self.added_new_lines
        ):
            raise ValueError("ChangeAtom added lines must be inside new_span")
        for position in self.diff_positions:
            allowed_lines = (
                self.deleted_old_lines if position.side == "base" else self.added_new_lines
            )
            if position.source_line not in allowed_lines:
                raise ValueError(
                    "DiffPosition source_line must name a changed line on its side"
                )

        expected_id = _stable_id(
            "change-atom",
            self.identity_payload(
                kind=self.kind,
                old_source_ref_id=self.old_source_ref_id,
                new_source_ref_id=self.new_source_ref_id,
                old_span=self.old_span,
                new_span=self.new_span,
                added_new_lines=self.added_new_lines,
                deleted_old_lines=self.deleted_old_lines,
                diff_positions=self.diff_positions,
                diff_normalizer_version=self.diff_normalizer_version,
            ),
        )
        if self.atom_id != expected_id:
            raise ValueError("ChangeAtom.atom_id does not match normalized content")


@dataclass(frozen=True)
class ChangedFile:
    changed_file_id: str
    status: ChangedFileStatus
    old_path: str | None
    new_path: str | None
    old_source_ref_id: str | None
    new_source_ref_id: str | None
    atom_ids: tuple[str, ...]
    is_binary: bool = False

    @classmethod
    def create(
        cls,
        *,
        status: ChangedFileStatus,
        old_path: str | None,
        new_path: str | None,
        old_source_ref_id: str | None,
        new_source_ref_id: str | None,
        atom_ids: tuple[str, ...],
        is_binary: bool,
    ) -> ChangedFile:
        payload = cls.identity_payload(
            status=status,
            old_path=old_path,
            new_path=new_path,
            old_source_ref_id=old_source_ref_id,
            new_source_ref_id=new_source_ref_id,
            atom_ids=atom_ids,
            is_binary=is_binary,
        )
        return cls(
            changed_file_id=_stable_id("changed-file", payload),
            status=status,
            old_path=old_path,
            new_path=new_path,
            old_source_ref_id=old_source_ref_id,
            new_source_ref_id=new_source_ref_id,
            atom_ids=atom_ids,
            is_binary=is_binary,
        )

    @staticmethod
    def identity_payload(
        *,
        status: ChangedFileStatus,
        old_path: str | None,
        new_path: str | None,
        old_source_ref_id: str | None,
        new_source_ref_id: str | None,
        atom_ids: tuple[str, ...],
        is_binary: bool,
    ) -> dict[str, object]:
        return {
            "status": status,
            "old_path": old_path,
            "new_path": new_path,
            "old_source_ref_id": old_source_ref_id,
            "new_source_ref_id": new_source_ref_id,
            "atom_ids": list(atom_ids),
            "is_binary": is_binary,
        }

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.status not in _FILE_STATUSES:
            raise ValueError(f"unsupported ChangedFile status: {self.status}")
        for value, context in (
            (self.old_path, "ChangedFile.old_path"),
            (self.new_path, "ChangedFile.new_path"),
        ):
            if value is not None and value != normalize_review_path(value):
                raise ValueError(f"{context} must be normalized")
        for value, context in (
            (self.old_source_ref_id, "ChangedFile.old_source_ref_id"),
            (self.new_source_ref_id, "ChangedFile.new_source_ref_id"),
        ):
            if value is not None:
                _require_text(value, context)
        if not isinstance(self.atom_ids, tuple) or any(
            not isinstance(atom_id, str) or not atom_id for atom_id in self.atom_ids
        ):
            raise ValueError("ChangedFile.atom_ids must contain non-empty strings")
        if len(self.atom_ids) != len(set(self.atom_ids)):
            raise ValueError("ChangedFile.atom_ids must be unique")
        if not isinstance(self.is_binary, bool):
            raise ValueError("ChangedFile.is_binary must be a boolean")

        if self.status == "added" and not (
            self.old_path is None and self.new_path is not None
        ):
            raise ValueError("added ChangedFile requires only new_path")
        if self.status == "deleted" and not (
            self.old_path is not None and self.new_path is None
        ):
            raise ValueError("deleted ChangedFile requires only old_path")
        if self.status == "modified" and not (
            self.old_path is not None and self.old_path == self.new_path
        ):
            raise ValueError("modified ChangedFile requires equal old/new paths")
        if self.status == "renamed" and not (
            self.old_path is not None
            and self.new_path is not None
            and self.old_path != self.new_path
        ):
            raise ValueError("renamed ChangedFile requires different old/new paths")

        if self.is_binary:
            if self.old_source_ref_id or self.new_source_ref_id or self.atom_ids:
                raise ValueError("binary ChangedFile must not fabricate source or atoms")
        else:
            expected_old = self.status in {"modified", "deleted", "renamed"}
            expected_new = self.status in {"added", "modified", "renamed"}
            if (self.old_source_ref_id is not None) != expected_old:
                raise ValueError("ChangedFile base source does not match status")
            if (self.new_source_ref_id is not None) != expected_new:
                raise ValueError("ChangedFile head source does not match status")
            if self.status == "modified" and not self.atom_ids:
                raise ValueError("modified ChangedFile requires at least one ChangeAtom")

        expected_id = _stable_id(
            "changed-file",
            self.identity_payload(
                status=self.status,
                old_path=self.old_path,
                new_path=self.new_path,
                old_source_ref_id=self.old_source_ref_id,
                new_source_ref_id=self.new_source_ref_id,
                atom_ids=self.atom_ids,
                is_binary=self.is_binary,
            ),
        )
        if self.changed_file_id != expected_id:
            raise ValueError("ChangedFile.changed_file_id does not match normalized content")


@dataclass(frozen=True)
class ChangeSetDiagnostic:
    diagnostic_id: str
    code: ChangeSetDiagnosticCode
    changed_file_id: str

    @classmethod
    def create(
        cls,
        *,
        code: ChangeSetDiagnosticCode,
        changed_file_id: str,
    ) -> ChangeSetDiagnostic:
        payload = {"code": code, "changed_file_id": changed_file_id}
        return cls(
            diagnostic_id=_stable_id("change-diagnostic", payload),
            code=code,
            changed_file_id=changed_file_id,
        )

    def __post_init__(self) -> None:
        if self.code not in _DIAGNOSTIC_CODES:
            raise ValueError(f"unsupported ChangeSet diagnostic code: {self.code}")
        _require_text(self.changed_file_id, "ChangeSetDiagnostic.changed_file_id")
        expected = _stable_id(
            "change-diagnostic",
            {"code": self.code, "changed_file_id": self.changed_file_id},
        )
        if self.diagnostic_id != expected:
            raise ValueError(
                "ChangeSetDiagnostic.diagnostic_id does not match normalized content"
            )


@dataclass(frozen=True)
class ChangeSet:
    schema_version: str
    change_set_id: str
    repository: str
    base_revision: str
    head_revision: str
    diff_normalizer_version: str
    source_refs: tuple[CodeSourceRef, ...] = ()
    files: tuple[ChangedFile, ...] = ()
    atoms: tuple[ChangeAtom, ...] = ()
    diagnostics: tuple[ChangeSetDiagnostic, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        repository: str,
        base_revision: str,
        head_revision: str,
        diff_normalizer_version: str,
        source_refs: tuple[CodeSourceRef, ...],
        files: tuple[ChangedFile, ...],
        atoms: tuple[ChangeAtom, ...],
        diagnostics: tuple[ChangeSetDiagnostic, ...],
    ) -> ChangeSet:
        payload = cls.identity_payload(
            repository=repository,
            base_revision=base_revision,
            head_revision=head_revision,
            diff_normalizer_version=diff_normalizer_version,
            source_refs=source_refs,
            files=files,
            atoms=atoms,
            diagnostics=diagnostics,
        )
        return cls(
            schema_version=CHANGE_SET_SCHEMA_VERSION,
            change_set_id=_stable_id("change-set", payload),
            repository=repository,
            base_revision=base_revision,
            head_revision=head_revision,
            diff_normalizer_version=diff_normalizer_version,
            source_refs=source_refs,
            files=files,
            atoms=atoms,
            diagnostics=diagnostics,
        )

    @staticmethod
    def identity_payload(
        *,
        repository: str,
        base_revision: str,
        head_revision: str,
        diff_normalizer_version: str,
        source_refs: tuple[CodeSourceRef, ...],
        files: tuple[ChangedFile, ...],
        atoms: tuple[ChangeAtom, ...],
        diagnostics: tuple[ChangeSetDiagnostic, ...],
    ) -> dict[str, object]:
        return {
            "schema_version": CHANGE_SET_SCHEMA_VERSION,
            "repository": repository,
            "base_revision": base_revision,
            "head_revision": head_revision,
            "diff_normalizer_version": diff_normalizer_version,
            "source_refs": [asdict(item) for item in source_refs],
            "files": [asdict(item) for item in files],
            "atoms": [asdict(item) for item in atoms],
            "diagnostics": [asdict(item) for item in diagnostics],
        }

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.schema_version != CHANGE_SET_SCHEMA_VERSION:
            raise ValueError(
                f"ChangeSet.schema_version must be {CHANGE_SET_SCHEMA_VERSION!r}"
            )
        for value, context in (
            (self.repository, "ChangeSet.repository"),
            (self.base_revision, "ChangeSet.base_revision"),
            (self.head_revision, "ChangeSet.head_revision"),
            (self.diff_normalizer_version, "ChangeSet.diff_normalizer_version"),
        ):
            _require_text(value, context)
        for values, item_type, context in (
            (self.source_refs, CodeSourceRef, "ChangeSet.source_refs"),
            (self.files, ChangedFile, "ChangeSet.files"),
            (self.atoms, ChangeAtom, "ChangeSet.atoms"),
            (self.diagnostics, ChangeSetDiagnostic, "ChangeSet.diagnostics"),
        ):
            if not isinstance(values, tuple) or any(
                not isinstance(item, item_type) for item in values
            ):
                raise ValueError(f"{context} contains an invalid value")

        source_ids = [item.source_ref_id for item in self.source_refs]
        if source_ids != sorted(set(source_ids)):
            raise ValueError("ChangeSet.source_refs must use unique stable ID order")
        source_by_id = {item.source_ref_id: item for item in self.source_refs}
        for source in self.source_refs:
            source.validate()
            if source.repository != self.repository:
                raise ValueError("ChangeSet source repository does not match")
            if source.revision not in {self.base_revision, self.head_revision}:
                raise ValueError("ChangeSet source revision is not a base/head endpoint")

        file_keys = [_changed_file_sort_key(item) for item in self.files]
        if file_keys != sorted(file_keys):
            raise ValueError("ChangeSet.files must use stable path order")
        file_ids = [item.changed_file_id for item in self.files]
        if len(file_ids) != len(set(file_ids)):
            raise ValueError("ChangeSet.files must have unique changed_file_id values")
        old_paths = [item.old_path for item in self.files if item.old_path is not None]
        new_paths = [item.new_path for item in self.files if item.new_path is not None]
        if len(old_paths) != len(set(old_paths)) or len(new_paths) != len(set(new_paths)):
            raise ValueError("ChangeSet files must use each base/head path at most once")

        atom_ids = [item.atom_id for item in self.atoms]
        if len(atom_ids) != len(set(atom_ids)):
            raise ValueError("ChangeSet.atoms must have unique atom_id values")
        atom_by_id = {item.atom_id: item for item in self.atoms}
        for versioned_atom in self.atoms:
            if versioned_atom.diff_normalizer_version != self.diff_normalizer_version:
                raise ValueError(
                    "ChangeAtom normalizer version must match ChangeSet"
                )
        expected_atom_order = sorted(
            self.atoms,
            key=lambda atom: _change_atom_sort_key(atom, source_by_id),
        )
        if list(self.atoms) != expected_atom_order:
            raise ValueError("ChangeSet.atoms must use stable source order")

        used_atom_ids: list[str] = []
        for changed_file in self.files:
            changed_file.validate()
            if changed_file.old_source_ref_id is not None:
                old_ref = source_by_id.get(changed_file.old_source_ref_id)
                if (
                    old_ref is None
                    or old_ref.revision != self.base_revision
                    or old_ref.path != changed_file.old_path
                ):
                    raise ValueError("ChangedFile base source reference is inconsistent")
            if changed_file.new_source_ref_id is not None:
                new_ref = source_by_id.get(changed_file.new_source_ref_id)
                if (
                    new_ref is None
                    or new_ref.revision != self.head_revision
                    or new_ref.path != changed_file.new_path
                ):
                    raise ValueError("ChangedFile head source reference is inconsistent")
            file_atoms: list[ChangeAtom] = []
            for atom_id in changed_file.atom_ids:
                referenced_atom = atom_by_id.get(atom_id)
                if referenced_atom is None:
                    raise ValueError("ChangedFile.atom_ids contains a dangling reference")
                if (
                    referenced_atom.old_source_ref_id is not None
                    and referenced_atom.old_source_ref_id
                    != changed_file.old_source_ref_id
                ) or (
                    referenced_atom.new_source_ref_id is not None
                    and referenced_atom.new_source_ref_id
                    != changed_file.new_source_ref_id
                ):
                    raise ValueError("ChangeAtom source references do not match its file")
                file_atoms.append(referenced_atom)
                used_atom_ids.append(atom_id)
            expected_file_atom_ids = tuple(
                item.atom_id
                for item in sorted(
                    file_atoms,
                    key=lambda atom: _change_atom_sort_key(atom, source_by_id),
                )
            )
            if changed_file.atom_ids != expected_file_atom_ids:
                raise ValueError("ChangedFile.atom_ids must use stable source order")
            deleted_lines = [
                line for atom in file_atoms for line in atom.deleted_old_lines
            ]
            added_lines = [
                line for atom in file_atoms for line in atom.added_new_lines
            ]
            if len(deleted_lines) != len(set(deleted_lines)) or len(
                added_lines
            ) != len(set(added_lines)):
                raise ValueError(
                    "ChangedFile lines must belong to one ChangeAtom per source side"
                )
        if sorted(used_atom_ids) != sorted(atom_ids):
            raise ValueError("every ChangeAtom must belong to exactly one ChangedFile")
        referenced_source_ids = {
            source_ref_id
            for changed_file in self.files
            for source_ref_id in (
                changed_file.old_source_ref_id,
                changed_file.new_source_ref_id,
            )
            if source_ref_id is not None
        }
        if referenced_source_ids != set(source_ids):
            raise ValueError("every CodeSourceRef must belong to a ChangedFile")

        diagnostic_keys = [
            (item.changed_file_id, item.code, item.diagnostic_id)
            for item in self.diagnostics
        ]
        if diagnostic_keys != sorted(set(diagnostic_keys)):
            raise ValueError("ChangeSet.diagnostics must be sorted and unique")
        diagnostic_by_file = {
            item.changed_file_id: item for item in self.diagnostics
        }
        if len(diagnostic_by_file) != len(self.diagnostics):
            raise ValueError("ChangeSet allows one diagnostic per binary file")
        for changed_file in self.files:
            diagnostic = diagnostic_by_file.get(changed_file.changed_file_id)
            if changed_file.is_binary:
                if diagnostic is None or diagnostic.code != "binary_source_unavailable":
                    raise ValueError("binary ChangedFile requires a structured diagnostic")
            elif diagnostic is not None:
                raise ValueError("source-backed ChangedFile must not have binary diagnostic")
        if set(diagnostic_by_file) != set(file_ids).intersection(diagnostic_by_file):
            raise ValueError("ChangeSet diagnostic references an unknown ChangedFile")

        expected_id = _stable_id(
            "change-set",
            self.identity_payload(
                repository=self.repository,
                base_revision=self.base_revision,
                head_revision=self.head_revision,
                diff_normalizer_version=self.diff_normalizer_version,
                source_refs=self.source_refs,
                files=self.files,
                atoms=self.atoms,
                diagnostics=self.diagnostics,
            ),
        )
        if self.change_set_id != expected_id:
            raise ValueError("ChangeSet.change_set_id does not match normalized content")

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(json.dumps(asdict(self))))


def normalize_change_set(
    *,
    repository: str,
    base_revision: str,
    head_revision: str,
    files: tuple[ChangedFileInput, ...],
    diff_normalizer_version: str = DIFF_NORMALIZER_VERSION,
) -> ChangeSet:
    """Normalize already-structured diff input without parsing Git or touching a repo."""

    _require_text(repository, "repository")
    _require_text(base_revision, "base_revision")
    _require_text(head_revision, "head_revision")
    _require_text(diff_normalizer_version, "diff_normalizer_version")
    if not isinstance(files, tuple) or any(
        not isinstance(item, ChangedFileInput) for item in files
    ):
        raise ValueError("files must contain ChangedFileInput values")

    normalized_files: list[ChangedFile] = []
    all_atoms: list[ChangeAtom] = []
    source_by_id: dict[str, CodeSourceRef] = {}

    for file_input in files:
        old_snapshot, new_snapshot = _validate_file_input(
            file_input,
            repository=repository,
            base_revision=base_revision,
            head_revision=head_revision,
        )
        if file_input.is_binary:
            changed_file = ChangedFile.create(
                status=file_input.status,
                old_path=file_input.old_path,
                new_path=file_input.new_path,
                old_source_ref_id=None,
                new_source_ref_id=None,
                atom_ids=(),
                is_binary=True,
            )
            normalized_files.append(changed_file)
            continue

        file_atoms = [
            _normalize_atom(
                atom_input,
                old_snapshot=old_snapshot,
                new_snapshot=new_snapshot,
                diff_normalizer_version=diff_normalizer_version,
            )
            for atom_input in file_input.atoms
        ]
        local_source_by_id = {
            snapshot.source_ref.source_ref_id: snapshot.source_ref
            for snapshot in (old_snapshot, new_snapshot)
            if snapshot is not None
        }
        file_atoms.sort(
            key=lambda atom: _change_atom_sort_key(atom, local_source_by_id)
        )
        _validate_file_atom_semantics(file_input, old_snapshot, new_snapshot, file_atoms)
        atom_ids = tuple(atom.atom_id for atom in file_atoms)
        changed_file = ChangedFile.create(
            status=file_input.status,
            old_path=file_input.old_path,
            new_path=file_input.new_path,
            old_source_ref_id=(
                None if old_snapshot is None else old_snapshot.source_ref.source_ref_id
            ),
            new_source_ref_id=(
                None if new_snapshot is None else new_snapshot.source_ref.source_ref_id
            ),
            atom_ids=atom_ids,
            is_binary=False,
        )
        normalized_files.append(changed_file)
        all_atoms.extend(file_atoms)
        for snapshot in (old_snapshot, new_snapshot):
            if snapshot is not None:
                source_by_id[snapshot.source_ref.source_ref_id] = snapshot.source_ref

    normalized_files.sort(key=_changed_file_sort_key)
    _validate_file_path_uniqueness(normalized_files)
    all_atoms.sort(key=lambda atom: _change_atom_sort_key(atom, source_by_id))
    source_refs = tuple(sorted(source_by_id.values(), key=lambda item: item.source_ref_id))
    diagnostics = tuple(
        sorted(
            (
                ChangeSetDiagnostic.create(
                    code="binary_source_unavailable",
                    changed_file_id=item.changed_file_id,
                )
                for item in normalized_files
                if item.is_binary
            ),
            key=lambda item: (item.changed_file_id, item.code, item.diagnostic_id),
        )
    )
    return ChangeSet.create(
        repository=repository,
        base_revision=base_revision,
        head_revision=head_revision,
        diff_normalizer_version=diff_normalizer_version,
        source_refs=source_refs,
        files=tuple(normalized_files),
        atoms=tuple(all_atoms),
        diagnostics=diagnostics,
    )


def _validate_file_input(
    file_input: ChangedFileInput,
    *,
    repository: str,
    base_revision: str,
    head_revision: str,
) -> tuple[CodeSourceSnapshot | None, CodeSourceSnapshot | None]:
    status = file_input.status
    old_snapshot = file_input.old_snapshot
    new_snapshot = file_input.new_snapshot
    if status == "added" and not (
        file_input.old_path is None and file_input.new_path is not None
    ):
        raise ValueError("added file input requires only new_path")
    if status == "deleted" and not (
        file_input.old_path is not None and file_input.new_path is None
    ):
        raise ValueError("deleted file input requires only old_path")
    if status == "modified" and not (
        file_input.old_path is not None and file_input.old_path == file_input.new_path
    ):
        raise ValueError("modified file input requires equal old/new paths")
    if status == "renamed" and not (
        file_input.old_path is not None
        and file_input.new_path is not None
        and file_input.old_path != file_input.new_path
    ):
        raise ValueError("renamed file input requires different old/new paths")

    if file_input.is_binary:
        if old_snapshot is not None or new_snapshot is not None or file_input.atoms:
            raise ValueError("binary file input must not fabricate source text or atoms")
        return None, None

    expected_old = status in {"modified", "deleted", "renamed"}
    expected_new = status in {"added", "modified", "renamed"}
    if (old_snapshot is not None) != expected_old:
        raise ValueError("file input base snapshot does not match status")
    if (new_snapshot is not None) != expected_new:
        raise ValueError("file input head snapshot does not match status")

    for snapshot, expected_revision, expected_path, role in (
        (old_snapshot, base_revision, file_input.old_path, "base"),
        (new_snapshot, head_revision, file_input.new_path, "head"),
    ):
        if snapshot is None:
            continue
        source = snapshot.source_ref
        if source.repository != repository:
            raise ValueError(f"{role} snapshot repository does not match ChangeSet")
        if source.revision != expected_revision:
            raise ValueError(f"{role} snapshot revision does not match ChangeSet endpoint")
        if source.path != expected_path:
            raise ValueError(f"{role} snapshot path does not match ChangedFileInput")
    return old_snapshot, new_snapshot


def _normalize_atom(
    atom_input: ChangeAtomInput,
    *,
    old_snapshot: CodeSourceSnapshot | None,
    new_snapshot: CodeSourceSnapshot | None,
    diff_normalizer_version: str,
) -> ChangeAtom:
    old_span = (
        None
        if atom_input.old_span is None or old_snapshot is None
        else _exact_line_range(old_snapshot, atom_input.old_span)
    )
    new_span = (
        None
        if atom_input.new_span is None or new_snapshot is None
        else _exact_line_range(new_snapshot, atom_input.new_span)
    )
    if atom_input.old_span is not None and old_snapshot is None:
        raise ValueError("ChangeAtomInput old_span requires a base snapshot")
    if atom_input.new_span is not None and new_snapshot is None:
        raise ValueError("ChangeAtomInput new_span requires a head snapshot")
    return ChangeAtom.create(
        kind=atom_input.kind,
        old_source_ref_id=(
            old_snapshot.source_ref.source_ref_id
            if old_span is not None and old_snapshot is not None
            else None
        ),
        new_source_ref_id=(
            new_snapshot.source_ref.source_ref_id
            if new_span is not None and new_snapshot is not None
            else None
        ),
        old_span=old_span,
        new_span=new_span,
        added_new_lines=atom_input.added_new_lines,
        deleted_old_lines=atom_input.deleted_old_lines,
        diff_positions=atom_input.diff_positions,
        diff_normalizer_version=diff_normalizer_version,
    )


def _validate_file_atom_semantics(
    file_input: ChangedFileInput,
    old_snapshot: CodeSourceSnapshot | None,
    new_snapshot: CodeSourceSnapshot | None,
    atoms: list[ChangeAtom],
) -> None:
    old_lines = [line for atom in atoms for line in atom.deleted_old_lines]
    new_lines = [line for atom in atoms for line in atom.added_new_lines]
    if len(old_lines) != len(set(old_lines)) or len(new_lines) != len(set(new_lines)):
        raise ValueError("changed lines must belong to exactly one ChangeAtom per side")
    if file_input.status == "added":
        if new_snapshot is None:
            raise ValueError("added file requires a head snapshot")
        if any(atom.kind != "addition" for atom in atoms):
            raise ValueError("added file may contain only addition atoms")
        expected = list(range(1, _line_count(new_snapshot.content) + 1))
        if sorted(new_lines) != expected:
            raise ValueError("added file atoms must cover every head source line")
    elif file_input.status == "deleted":
        if old_snapshot is None:
            raise ValueError("deleted file requires a base snapshot")
        if any(atom.kind != "deletion" for atom in atoms):
            raise ValueError("deleted file may contain only deletion atoms")
        expected = list(range(1, _line_count(old_snapshot.content) + 1))
        if sorted(old_lines) != expected:
            raise ValueError("deleted file atoms must cover every base source line")
    else:
        if old_snapshot is None or new_snapshot is None:
            raise ValueError("modified/renamed file requires base and head snapshots")
        if old_snapshot.source_ref.content_hash == new_snapshot.source_ref.content_hash:
            if file_input.status != "renamed" or atoms:
                raise ValueError("unchanged source is valid only for a pure rename")
        elif not atoms:
            raise ValueError("edited source requires at least one ChangeAtom")


def _changed_file_sort_key(item: ChangedFile) -> tuple[str, str, str, str]:
    return (
        item.old_path or "",
        item.new_path or "",
        item.status,
        item.changed_file_id,
    )


def _change_atom_sort_key(
    atom: ChangeAtom,
    source_by_id: dict[str, CodeSourceRef],
) -> tuple[object, ...]:
    old_ref = (
        None
        if atom.old_source_ref_id is None
        else source_by_id.get(atom.old_source_ref_id)
    )
    new_ref = (
        None
        if atom.new_source_ref_id is None
        else source_by_id.get(atom.new_source_ref_id)
    )
    if atom.old_source_ref_id is not None and old_ref is None:
        raise ValueError("ChangeAtom references an unknown base source")
    if atom.new_source_ref_id is not None and new_ref is None:
        raise ValueError("ChangeAtom references an unknown head source")
    return (
        "" if old_ref is None else old_ref.path,
        "" if new_ref is None else new_ref.path,
        0 if atom.old_span is None else atom.old_span.start_line,
        0 if atom.old_span is None else atom.old_span.end_line,
        0 if atom.new_span is None else atom.new_span.start_line,
        0 if atom.new_span is None else atom.new_span.end_line,
        atom.kind,
        atom.atom_id,
    )


def _validate_file_path_uniqueness(files: list[ChangedFile]) -> None:
    old_paths = [item.old_path for item in files if item.old_path is not None]
    new_paths = [item.new_path for item in files if item.new_path is not None]
    if len(old_paths) != len(set(old_paths)) or len(new_paths) != len(set(new_paths)):
        raise ValueError("files must use each base/head path at most once")
